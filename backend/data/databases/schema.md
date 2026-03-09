## Database schema diagrams (generated from `schema.txt`)

Note: Column names with spaces in the original schema were converted to snake_case for diagram compatibility. Primary keys are marked where explicitly defined.

### data_context.db

```mermaid
erDiagram
    GENERATION_STATUS {
        TEXT generation_id PK
        TEXT user_id
        TEXT status
        INTEGER progress
        INTEGER total_fields
        INTEGER completed_fields
        TEXT current_field
        TEXT whitepaper_type
        TEXT results
        TEXT error_message
        TIMESTAMP started_at
        TIMESTAMP updated_at
    }
    USER_CONTEXT {
        INTEGER id PK
        TEXT auth0_user_id
        TEXT context_data
        TIMESTAMP created_at
        TIMESTAMP updated_at
    }
```

### guidelines.db

```mermaid
erDiagram
    GUIDELINES_ART {
        VARCHAR No
        VARCHAR FIELD
        VARCHAR content_to_be_reported
        VARCHAR form_and_standards_to_be_used_for_reporting
        VARCHAR example_of_good_answer
        VARCHAR example_of_bad_answer
        VARCHAR section_name
        INT order_in_section
    }
    GUIDELINES_EMT {
        VARCHAR No
        VARCHAR FIELD
        VARCHAR content_to_be_reported
        VARCHAR form_and_standards_to_be_used_for_reporting
        VARCHAR example_of_good_answer
        VARCHAR example_of_bad_answer
        VARCHAR section_name
        INT order_in_section
    }
    GUIDELINES_OTH {
        VARCHAR No
        VARCHAR FIELD
        VARCHAR content_to_be_reported
        VARCHAR form_and_standards_to_be_used_for_reporting
        VARCHAR example_of_good_answer
        VARCHAR example_of_bad_answer
        VARCHAR section_name
        INT order_in_section
    }
```

### subquestions.db

```mermaid
erDiagram
    SUBQUESTIONS_ART {
        VARCHAR field_id
        VARCHAR question
        VARCHAR type
        VARCHAR relevant_field
        VARCHAR relevant_variable
    }
    SUBQUESTIONS_EMT {
        VARCHAR field_id
        VARCHAR question
        VARCHAR type
        VARCHAR relevant_field
        VARCHAR relevant_variable
    }
    SUBQUESTIONS_OTH {
        VARCHAR field_id
        VARCHAR question
        VARCHAR type
        VARCHAR relevant_field
        VARCHAR relevant_variable
    }
```

### oth_whitepaper_fields.db

```mermaid
erDiagram
    OTH_SECTION1 {
        INTEGER id PK
        TEXT field_name
        TEXT field_id
    }
    OTH_SECTION2 {
        INTEGER id PK
        TEXT field_name
        TEXT field_id
    }
    OTH_SECTION3 {
        INTEGER id PK
        TEXT field_name
        TEXT field_id
    }
    OTH_SECTION4 {
        INTEGER id PK
        TEXT field_name
        TEXT field_id
    }
    OTH_SECTION5 {
        INTEGER id PK
        VARCHAR field_name
        VARCHAR field_id
    }
    OTH_SECTION6 {
        INTEGER id PK
        VARCHAR field_name
        VARCHAR field_id
    }
    OTH_SECTION7 {
        INTEGER id PK
        TEXT field_name
        TEXT field_id
    }
    OTH_SECTION8 {
        INTEGER id PK
        TEXT field_name
        TEXT field_id
    }
    OTH_SECTION9 {
        INTEGER id PK
        TEXT field_name
        TEXT field_id
    }
    OTH_SECTION10 {
        INTEGER id PK
        TEXT field_name
        TEXT field_id
    }
    OTH_SECTION11 {
        INTEGER id PK
        TEXT field_name
        TEXT field_id
    }
    OTH_SECTION13 {
        INTEGER id PK
        TEXT field_name
        TEXT field_id
    }
```

### emt_whitepaper_fields.db

```mermaid
erDiagram
    EMT_SECTION1 {
        INTEGER id PK
        TEXT field_name
        TEXT field_id
    }
    EMT_SECTION2 {
        INTEGER id PK
        TEXT field_name
        TEXT field_id
    }
    EMT_SECTION3 {
        INTEGER id PK
        TEXT field_name
        TEXT field_id
    }
    EMT_SECTION4 {
        INTEGER id PK
        TEXT field_name
        TEXT field_id
    }
    EMT_SECTION5 {
        INTEGER id PK
        VARCHAR field_name
        VARCHAR field_id
    }
    EMT_SECTION6 {
        INTEGER id PK
        VARCHAR field_name
        VARCHAR field_id
    }
    EMT_SECTION7 {
        INTEGER id PK
        TEXT field_name
        TEXT field_id
    }
    EMT_SECTION8 {
        INTEGER id PK
        TEXT field_name
        TEXT field_id
    }
    EMT_SECTION13 {
        INTEGER id PK
        TEXT field_name
        TEXT field_id
    }
```

### art_whitepaper_fields.db

```mermaid
erDiagram
    ART_SECTION1 {
        INTEGER id PK
        TEXT field_name
        TEXT field_id
    }
    ART_SECTION2 {
        INTEGER id PK
        TEXT field_name
        TEXT field_id
    }
    ART_SECTION3 {
        INTEGER id PK
        TEXT field_name
        TEXT field_id
    }
    ART_SECTION4 {
        INTEGER id PK
        TEXT field_name
        TEXT field_id
    }
    ART_SECTION5 {
        INTEGER id PK
        VARCHAR field_name
        VARCHAR field_id
    }
    ART_SECTION6 {
        INTEGER id PK
        VARCHAR field_name
        VARCHAR field_id
    }
    ART_SECTION7 {
        INTEGER id PK
        TEXT field_name
        TEXT field_id
    }
    ART_SECTION8 {
        INTEGER id PK
        TEXT field_name
        TEXT field_id
    }
    ART_SECTION9 {
        INTEGER id PK
        TEXT field_name
        TEXT field_id
    }
    ART_SECTION10 {
        INTEGER id PK
        TEXT field_name
        TEXT field_id
    }
    ART_SECTION13 {
        INTEGER id PK
        TEXT field_name
        TEXT field_id
    }
```

<!-- If you want this rendered as SVG/PNG, you can use Mermaid-compatible renderers or the mermaid-cli (mmdc). -->


