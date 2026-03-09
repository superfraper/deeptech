# ESF Dash RAG - Architecture Documentation

## 🏗️ **Architecture Overview**

### **System Architecture**

The ESF Dash RAG is a modern **Retrieval-Augmented Generation (RAG)** system designed for processing whitepaper documents with secure authentication. It follows a **microservices architecture** deployed via containers:

**3-Tier Architecture:**

- **Frontend**: React SPA with Auth0 authentication
- **Backend**: FastAPI-based Python API with JWT validation
- **Infrastructure**: Nginx reverse proxy with SSL termination + external cloud services

**Data Flow:**

```
User → Auth0 → React Frontend → Nginx Proxy → FastAPI Backend → AWS OpenSearch/S3 + OpenAI API
```

### **Core Components**

1. **Document Processing Pipeline**
   - PDF parsing using Docling
   - Text chunking with contextual embeddings
   - Vector storage in AWS OpenSearch
   - File storage in AWS S3

2. **RAG Implementation**
   - OpenAI text-embedding-ada-002 for vectorization
   - Hybrid search combining vector similarity + filtering
   - Context-aware chunk generation
   - Field-specific content generation

3. **Database Layer**
   - SQLite databases for different token types (ART, EMT, OTH)
   - User context persistence
   - Dynamic database routing based on whitepaper type

## 🛠️ **Technology Stack**

### **Frontend Stack**

- **Framework**: React 18.3.1 with functional components
- **Routing**: React Router v6 for SPA navigation
- **Authentication**: Auth0 React SDK (@auth0/auth0-react)
- **Styling**: Tailwind CSS for utility-first styling
- **State Management**: React Context API with custom providers
- **PDF Handling**: React-PDF for document viewing
- **UI Components**: Hero Icons, custom components
- **Build**: Create React App with custom webpack config

### **Backend Stack**

- **Framework**: FastAPI (Python 3.9+) for high-performance async API
- **Authentication**: PyJWT with RS256 for Auth0 JWT validation
- **Database**: SQLite with custom DatabaseHandler abstraction
- **AI/ML**: OpenAI API for embeddings and LLM responses
- **Search**: AWS OpenSearch Serverless for vector similarity search
- **Document Processing**:
  - Docling for PDF parsing
  - TikToken for intelligent text chunking
  - Selenium + webdriver for web scraping
- **Cloud Services**: Boto3 for AWS S3 integration
- **Validation**: Pydantic for request/response models

### **Infrastructure & DevOps**

- **Containerization**: Docker with multi-stage builds
- **Orchestration**: Docker Compose for local development
- **Reverse Proxy**: Nginx with rate limiting and security headers
- **SSL**: Let's Encrypt certificates with auto-renewal
- **Deployment**: AWS EC2 with containerized deployment
- **Monitoring**: Structured logging with file persistence

## 🔒 **Security Implementation**

### **Authentication & Authorization**

- **Auth0 Integration**: Complete SSO with JWT tokens
- **JWT Validation**: RS256 algorithm with JWKS verification
- **Token Scope**: `openid profile email read:current_user`
- **Route Protection**: All API endpoints require valid JWT
- **User Context**: Secure user-specific data isolation

### **Infrastructure Security**

```nginx
# Security Headers
Strict-Transport-Security: max-age=31536000; includeSubDomains
X-Frame-Options: SAMEORIGIN
X-Content-Type-Options: nosniff
X-XSS-Protection: 1; mode=block
Referrer-Policy: strict-origin-when-cross-origin
```

- **HTTPS Enforcement**: Automatic HTTP→HTTPS redirects
- **Rate Limiting**: API endpoints (30 req/min), general (60 req/min)
- **CORS Configuration**: Restricted origins with credential support
- **Container Security**: Non-root user execution (uid 1000)
- **Environment Variables**: Secure secret management via .env files

### **Data Security**

- **User Isolation**: All data operations filtered by user_id
- **Database Access**: Parameterized queries preventing SQL injection
- **File Access**: S3 pre-signed URLs with user-specific prefixes
- **Vector Search**: User-filtered OpenSearch queries
- **Audit Logging**: Comprehensive request/response logging

## 📋 **Development Practices**

### **Code Organization**

```
backend/
├── app/
│   ├── core/           # Authentication, database handlers
│   ├── models/         # Pydantic schemas
│   ├── utils/          # Business logic (generate, retrieve, search)
│   └── config.py       # Centralized configuration
├── data/               # SQLite databases and JSON data
└── main.py            # FastAPI application entry

frontend/
├── src/
│   ├── components/     # Reusable UI components
│   ├── context/        # State management
│   ├── services/       # API integration
│   └── App.js         # Main application with routing
```

### **Architecture Patterns**

- **Separation of Concerns**: Clear boundaries between auth, business logic, and data
- **Dependency Injection**: FastAPI's dependency system for auth and database handlers
- **Provider Pattern**: React Context for state management across components
- **Repository Pattern**: DatabaseHandler abstraction for data access
- **Factory Pattern**: Dynamic database selection based on token classification

### **Data Management**

- **Contextual State**: React Context providers for different data domains
- **Auto-save**: Debounced automatic persistence (2-second delay)
- **User Context**: Persistent storage of user session data
- **Field Management**: Tracked field acceptance and improvement states

## 🚀 **Deployment & Operations**

### **Containerization Strategy**

```dockerfile
# Multi-stage frontend build
FROM node:18-alpine as builder
# ... build React app
FROM nginx:alpine
# Copy built assets + custom nginx config

# Backend with security
FROM python:3.9-slim
# ... install dependencies
USER appuser  # Non-root execution
```

### **Production Configuration**

- **Health Checks**: Container health monitoring with curl endpoints
- **Volume Persistence**: Database and log file mounting
- **Service Discovery**: Docker Compose networking
- **SSL Termination**: Nginx-handled with certificate auto-renewal
- **Resource Limits**: Configured for t3.large+ EC2 instances

### **Monitoring & Maintenance**

- **Structured Logging**: JSON-formatted logs with log levels
- **Database Backups**: Automated SQLite backup scripts
- **SSL Renewal**: Automated Let's Encrypt certificate renewal
- **Health Endpoints**: `/health` for service monitoring
- **Error Handling**: Comprehensive exception handling with user-friendly messages

## 🎯 **Domain-Specific Features**

### **Whitepaper Processing**

- **Multi-Type Support**: ART, EMT, OTH token classifications
- **Dynamic Database Routing**: Context-aware database selection
- **Field Generation**: Intelligent form field population using RAG
- **Follow-up Questions**: Interactive field improvement system
- **LEI Lookup**: GLEIF API integration for legal entity validation
- **DTI Search**: Comprehensive token identification lookup

### **Performance Optimizations**

- **Chunking Strategy**: TikToken-based intelligent text segmentation
- **Vector Caching**: OpenSearch indexing with user isolation
- **Async Processing**: FastAPI async endpoints for I/O operations
- **Batch Operations**: Bulk document processing and deletion
- **Connection Pooling**: Efficient database and API connections

## 📊 **Key Metrics & Specifications**

### **Performance Targets**

- **Response Time**: < 2s for document upload processing
- **Concurrent Users**: Designed for 100+ simultaneous users
- **Document Size**: Supports PDFs up to 10MB
- **Vector Dimensions**: 1536-dimensional embeddings (OpenAI ada-002)
- **Search Results**: Top-5 relevant chunks with 0.7 similarity threshold

### **Scalability Considerations**

- **Horizontal Scaling**: Stateless backend services
- **Database Sharding**: User-based data partitioning
- **Caching Strategy**: Vector embeddings cached in OpenSearch
- **Load Balancing**: Nginx upstream configuration ready
- **Auto-scaling**: Docker Swarm or Kubernetes deployment ready

## 🔧 **Development Workflow**

### **Local Development**

```bash
# Start all services
docker-compose up -d --build

# Backend development
cd backend && uvicorn main:app --reload

# Frontend development
cd frontend && npm start
```

### **Environment Management**

- **Development**: Local Docker Compose with hot reload
- **Staging**: AWS EC2 with production-like configuration
- **Production**: AWS EC2 with SSL, monitoring, and backups

### **Code Quality**

- **Type Safety**: Pydantic models for API validation
- **Error Handling**: Comprehensive exception management
- **Logging**: Structured logging with correlation IDs
- **Documentation**: FastAPI auto-generated OpenAPI docs

## 🎯 **Business Logic Flow**

### **Document Processing Workflow**

1. **Authentication**: User authenticates via Auth0
2. **Upload**: PDF uploaded to S3, metadata stored
3. **Processing**: Document parsed, chunked, and embedded
4. **Indexing**: Chunks stored in OpenSearch with user isolation
5. **Generation**: RAG system generates field content based on context
6. **Validation**: User reviews and accepts/improves generated content
7. **Persistence**: Final data saved to user context

### **RAG Query Process**

1. **Query Embedding**: User question converted to vector
2. **Similarity Search**: OpenSearch finds relevant chunks
3. **Context Assembly**: Retrieved chunks combined with field context
4. **LLM Generation**: OpenAI generates field-specific response
5. **Post-processing**: Response formatted and validated
6. **User Feedback**: Follow-up questions for improvement

---

This system demonstrates **enterprise-grade architecture** with comprehensive security, scalable cloud integration, and modern development practices suitable for regulatory document processing in financial technology contexts.
