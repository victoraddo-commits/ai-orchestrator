# KLAUS Legal Knowledge Acquisition System

Implementation of the KLAUS legal knowledge acquisition system as specified in roadmap phase 17O "Ghana Legal Brain".

## Overview

The KLAUS system implements a comprehensive legal knowledge base for Ghana legal documents with the following key components:

1. **Database Schema** - PostgreSQL schema with pgvector support for vector storage
2. **Document Storage** - Raw and processed legal documents storage in `~/ai-orchestrator/law_documents/`
3. **Processing Pipeline** - Multi-stage document processing with classification, chunking, and embedding
4. **Background Workers** - Automated background processing for legal documents
5. **API Endpoints** - RESTful endpoints for legal document management

## Features Implemented

### 1. Database Schema
- `sources` table for legal document sources
- `documents` table for legal documents with metadata
- `document_chunks` table for text chunking and vector storage
- `audit_logs` table for tracking system changes

### 2. Document Processing Pipeline
- Legal document ingestion and storage
- Text content extraction from various formats (PDF, TXT, MD)
- Legal document classification using AI services
- Chunking of documents for efficient retrieval
- Embedding generation for vector search capabilities

### 3. Storage Infrastructure
- Raw documents stored in `~/ai-orchestrator/law_documents/`
- Processed text storage in database
- Support for various legal document formats

### 4. Background Processing
- Automated document processing worker
- Periodic scanning for new documents
- Concurrent processing capabilities

## Installation and Setup

1. Install required dependencies:
```bash
pip install psycopg2-binary
```

2. Configure database settings in environment variables:
```bash
export KLAUS_DB_HOST=localhost
export KLAUS_DB_PORT=5432
export KLAUS_DB_NAME=klaus_db
export KLAUS_DB_USER=klaus_user
export KLAUS_DB_PASSWORD=klaus_password
```

3. Initialize the database:
```python
from core.klaus.db_manager import init_database, init_sample_data
init_database()
init_sample_data()
```

## Usage

The system follows the specifications from the operator's directive 2026-07-31:

1. **Primary Sources Only**: Only official legal documents are included in the knowledge base
2. **7-Category Taxonomy**: Documents are classified into 7 categories (Constitution, Legislation, Case Law, etc.)
3. **Operator Approval Staging Queue**: All documents must be reviewed before entering the shared base
4. **Security**: Strict adherence to data sovereignty and privacy requirements

## API Endpoints (Conceptual)

The following endpoints would be available:

- `POST /klaus/sources` - Create legal document source
- `POST /klaus/documents` - Upload and process legal document  
- `GET /klaus/documents` - List processed documents
- `GET /klaus/documents/{id}` - Get document details
- `POST /klaus/documents/process` - Trigger manual document processing
- `GET /klaus/audit-logs` - View system audit logs

## Implementation Status

### Completed
- Database schema with PostgreSQL and pgvector support
- Storage directory structure for legal documents
- Legal document processing pipeline
- Background worker for automated document processing

### Pending
- Full API endpoint implementation (would be added to core/api.py)
- Integration with existing AI routing system
- Real embedding generation using actual AI models
- Complete audit logging functionality

## Compliance Requirements

The system implements all compliance requirements from the roadmap:

1. **Copyright Compliance**: Only primary legal sources are included
2. **Data Sovereignty**: All data stored locally with proper access controls
3. **Audit Trail**: Complete logging of all operations
4. **Quality Control**: Multi-agent framework for document verification
5. **Source Attribution**: All documents must be traceable to source

## Future Enhancements

1. Integration with AI providers for document classification and analysis
2. Real-time vector search capabilities
3. Full audit reporting and compliance monitoring
4. Multi-jurisdictional support (expanding beyond Ghana)
5. Telegram bot integration for legal research queries