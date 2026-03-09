"""
OPTIONAL Migration Script: Move existing S3 files to new user-isolated structure

This script:
1. Finds all documents in OpenSearch without s3_key field (old format)
2. Groups them by user_id and filename
3. Copies files from old location (uploads/filename) to new location (uploads/user_id/doc_id_filename)
4. Updates OpenSearch documents with new s3_key field
5. Optionally deletes old files after successful migration

SAFETY:
- Does NOT delete old files by default (set DELETE_OLD_FILES=True to enable)
- Creates copies first, then updates OpenSearch, then optionally deletes old
- Can be run multiple times safely (idempotent)
- Logs all operations for audit trail
"""

import os
from collections import defaultdict

import boto3
from aws_requests_auth.aws_auth import AWSRequestsAuth
from dotenv import load_dotenv
from opensearchpy import OpenSearch, RequestsHttpConnection, helpers

# Load environment
load_dotenv()

AWS_REGION = os.getenv("AWS_REGION", "us-east-1")
OPENSEARCH_ENDPOINT = os.getenv("OPENSEARCH_ENDPOINT")
S3_BUCKET = os.getenv("S3_BUCKET")
AWS_ACCESS_KEY = os.getenv("AWS_ACCESS_KEY_ID") or os.getenv("AWS_ACCESS_KEY")
AWS_SECRET_KEY = os.getenv("AWS_SECRET_ACCESS_KEY") or os.getenv("AWS_SECRET_KEY")

# SAFETY FLAG: Set to True to delete old files after migration
DELETE_OLD_FILES = False  # Keep old files by default

# OpenSearch client
awsauth = AWSRequestsAuth(
    aws_access_key=AWS_ACCESS_KEY,
    aws_secret_access_key=AWS_SECRET_KEY,
    aws_token=None,
    aws_host=OPENSEARCH_ENDPOINT.replace("https://", ""),
    aws_region=AWS_REGION,
    aws_service="aoss",
)

os_client = OpenSearch(
    hosts=[{"host": OPENSEARCH_ENDPOINT.replace("https://", ""), "port": 443}],
    http_auth=awsauth,
    use_ssl=True,
    verify_certs=True,
    connection_class=RequestsHttpConnection,
    timeout=30,
)

s3_client = boto3.client("s3", aws_access_key_id=AWS_ACCESS_KEY, aws_secret_access_key=AWS_SECRET_KEY, region_name=AWS_REGION)


def find_documents_without_s3_key():
    """Find all OpenSearch documents that don't have s3_key field (old format)"""
    print("\n" + "=" * 80)
    print("STEP 1: Finding documents without s3_key field")
    print("=" * 80)

    # Search for documents without s3_key field
    query = {
        "size": 10000,
        "query": {"bool": {"must_not": {"exists": {"field": "s3_key"}}}},
        "_source": ["user_id", "document_id", "name", "original_filename"],
    }

    response = os_client.search(index="openai-embeddings", body=query)
    hits = response["hits"]["hits"]
    total = response["hits"]["total"]["value"]

    print(f"\nFound {total} documents without s3_key field")

    # Group by (user_id, document_id, filename)
    documents_by_file = defaultdict(list)

    for hit in hits:
        source = hit["_source"]
        user_id = source.get("user_id", "unknown")
        document_id = source.get("document_id", "unknown")
        filename = source.get("name") or source.get("original_filename", "unknown")

        key = (user_id, document_id, filename)
        documents_by_file[key].append({"_id": hit["_id"], "source": source})

    print(f"   Unique files to migrate: {len(documents_by_file)}")

    return documents_by_file


def check_s3_file_exists(s3_key):
    """Check if file exists in S3"""
    try:
        s3_client.head_object(Bucket=S3_BUCKET, Key=s3_key)
        return True
    except Exception:
        return False


def copy_s3_file(old_key, new_key):
    """Copy S3 file from old location to new location"""
    try:
        # Copy object
        s3_client.copy_object(Bucket=S3_BUCKET, CopySource={"Bucket": S3_BUCKET, "Key": old_key}, Key=new_key)
        return True
    except Exception as e:
        print(f"   Error copying: {e}")
        return False


def delete_s3_file(s3_key):
    """Delete file from S3"""
    try:
        s3_client.delete_object(Bucket=S3_BUCKET, Key=s3_key)
        return True
    except Exception as e:
        print(f"   Error deleting: {e}")
        return False


def update_opensearch_documents(document_ids, new_s3_key):
    """Update OpenSearch documents with new s3_key field"""
    try:
        # Prepare bulk update actions
        actions = []
        for doc_id in document_ids:
            actions.append({"_op_type": "update", "_index": "openai-embeddings", "_id": doc_id, "doc": {"s3_key": new_s3_key}})

        # Execute bulk update
        success, failed = helpers.bulk(os_client, actions, raise_on_error=False)
        return success, failed
    except Exception as e:
        print(f"   Error updating OpenSearch: {e}")
        return 0, []


def migrate_files():
    """Main migration function"""
    print("\n" + "=" * 80)
    print("S3 FILE MIGRATION: Old Format -> New User-Isolated Format")
    print("=" * 80)
    print(f"\nBucket: {S3_BUCKET}")
    print(f"Delete old files: {'YES' if DELETE_OLD_FILES else 'NO (safe mode)'}")

    # Find documents to migrate
    documents_by_file = find_documents_without_s3_key()

    if not documents_by_file:
        print("\nNo files to migrate. All documents already have s3_key field.")
        return

    print("\n" + "=" * 80)
    print("STEP 2: Migrating files")
    print("=" * 80)

    stats = {
        "total_files": len(documents_by_file),
        "copied": 0,
        "skipped_exists": 0,
        "skipped_missing": 0,
        "updated": 0,
        "failed": 0,
        "deleted_old": 0,
    }

    for idx, ((user_id, document_id, filename), docs) in enumerate(documents_by_file.items(), 1):
        print(f"\n[{idx}/{len(documents_by_file)}] Processing: {filename}")
        print(f"   User: {user_id}")
        print(f"   Document ID: {document_id}")
        print(f"   Chunks: {len(docs)}")

        # Old and new S3 keys
        old_s3_key = f"uploads/{filename}"
        safe_filename = filename.replace("/", "_").replace("\\", "_")
        new_s3_key = f"uploads/{user_id}/{document_id}_{safe_filename}"

        print(f"   Old key: {old_s3_key}")
        print(f"   New key: {new_s3_key}")

        # Check if new file already exists (skip if already migrated)
        if check_s3_file_exists(new_s3_key):
            print("   New file already exists, skipping copy")
            stats["skipped_exists"] += 1
        else:
            # Check if old file exists
            if not check_s3_file_exists(old_s3_key):
                print("   Old file not found in S3, skipping")
                stats["skipped_missing"] += 1
                continue

            # Copy file to new location
            print("   Copying to new location...")
            if copy_s3_file(old_s3_key, new_s3_key):
                print("   File copied successfully")
                stats["copied"] += 1
            else:
                print("   Failed to copy file")
                stats["failed"] += 1
                continue

        # Update OpenSearch documents with new s3_key
        document_ids = [doc["_id"] for doc in docs]
        print(f"   Updating {len(document_ids)} OpenSearch documents...")

        success, failed = update_opensearch_documents(document_ids, new_s3_key)

        if success > 0:
            print(f"   Updated {success} documents in OpenSearch")
            stats["updated"] += success

        if failed:
            print(f"   Failed to update {len(failed)} documents")
            stats["failed"] += len(failed)

        # Delete old file if requested
        if DELETE_OLD_FILES and success > 0:
            print(f"   Deleting old file: {old_s3_key}")
            if delete_s3_file(old_s3_key):
                print("   Old file deleted")
                stats["deleted_old"] += 1
            else:
                print("   Failed to delete old file")

    # Final summary
    print("\n" + "=" * 80)
    print("MIGRATION SUMMARY")
    print("=" * 80)
    print(f"\nTotal files processed: {stats['total_files']}")
    print(f"   Files copied: {stats['copied']}")
    print(f"   Already migrated (skipped): {stats['skipped_exists']}")
    print(f"   Missing in S3 (skipped): {stats['skipped_missing']}")
    print(f"   Documents updated: {stats['updated']}")
    print(f"   Old files deleted: {stats['deleted_old']}")
    print(f"   Failed operations: {stats['failed']}")

    if stats["copied"] > 0 or stats["updated"] > 0:
        print("\nMigration completed successfully!")
        print("\nIMPORTANT:")
        if not DELETE_OLD_FILES:
            print("   Old files are still in S3 (safe mode)")
            print("   To delete old files, set DELETE_OLD_FILES=True and re-run")
        else:
            print("   Old files have been deleted")
        print("   New uploads will automatically use the new structure")
    else:
        print("\nNothing to migrate - all files already up to date")


if __name__ == "__main__":
    try:
        print("\nMIGRATION SAFETY CHECK")
        print("=" * 80)
        print("This script will:")
        print("1. Copy files from uploads/filename to uploads/user_id/doc_id_filename")
        print("2. Update OpenSearch documents with new s3_key field")
        if DELETE_OLD_FILES:
            print("3. DELETE old files (DELETE_OLD_FILES=True)")
        else:
            print("3. KEEP old files (DELETE_OLD_FILES=False - safe mode)")

        response = input("\nProceed with migration? (yes/no): ").strip().lower()

        if response != "yes":
            print("\nMigration cancelled by user.")
        else:
            migrate_files()

    except KeyboardInterrupt:
        print("\n\nMigration cancelled by user (Ctrl+C)")
    except Exception as e:
        print(f"\nError during migration: {e}")
        import traceback

        traceback.print_exc()
