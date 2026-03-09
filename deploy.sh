#!/bin/bash

# ESF Dash RAG Deployment Script
# This script helps with common deployment tasks

set -e

# Colors for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Function to print colored output
print_status() {
    echo -e "${GREEN}[INFO]${NC} $1"
}

print_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

# Check if .env file exists
check_env() {
    if [ ! -f .env ]; then
        print_error ".env file not found!"
        print_status "Creating .env from template..."
        cp .env.example .env
        print_warning "Please edit .env file with your actual values before proceeding."
        exit 1
    fi
}

# Check if SSL certificates exist
check_ssl() {
    if [ ! -f nginx/ssl/cert.pem ] || [ ! -f nginx/ssl/key.pem ]; then
        print_error "SSL certificates not found in nginx/ssl/"
        print_status "Please set up SSL certificates before deploying."
        print_status "See README.md for instructions."
        exit 1
    fi
}

# Build and deploy
deploy() {
    print_status "Checking prerequisites..."
    check_env
    check_ssl
    
    print_status "Building and starting services..."
    docker-compose up -d --build
    
    print_status "Waiting for services to start..."
    sleep 10
    
    print_status "Checking service health..."
    if curl -k https://localhost/health > /dev/null 2>&1; then
        print_status "Services are running!"
    else
        print_error "Health check failed. Check logs with: docker-compose logs"
        exit 1
    fi
}

# Stop all services
stop() {
    print_status "Stopping all services..."
    docker-compose down
}

# View logs
logs() {
    docker-compose logs -f "$@"
}

# Backup databases
backup() {
    print_status "Creating backup directory..."
    mkdir -p backups
    
    print_status "Backing up databases..."
    for db in backend/*.db; do
        if [ -f "$db" ]; then
            backup_name="backups/$(basename $db).$(date +%Y%m%d_%H%M%S)"
            cp "$db" "$backup_name"
            print_status "Backed up $(basename $db) to $backup_name"
        fi
    done
}

# Update from git and redeploy
update() {
    print_status "Pulling latest changes..."
    git pull origin main
    
    print_status "Rebuilding and redeploying..."
    deploy
}

# Main script
case "$1" in
    deploy)
        deploy
        ;;
    stop)
        stop
        ;;
    logs)
        shift
        logs "$@"
        ;;
    backup)
        backup
        ;;
    update)
        update
        ;;
    *)
        echo "Usage: $0 {deploy|stop|logs|backup|update}"
        echo ""
        echo "Commands:"
        echo "  deploy  - Build and start all services"
        echo "  stop    - Stop all services"
        echo "  logs    - View logs (optionally specify service)"
        echo "  backup  - Backup all databases"
        echo "  update  - Pull latest changes and redeploy"
        exit 1
        ;;
esac
