# Moroccan Official Gazette Scraper API Documentation

## Overview

The Moroccan Official Gazette Scraper is a FastAPI-based web service that scrapes bulletin data from the official Moroccan government gazette website (sgg.gov.ma). It provides endpoints to retrieve bulletin information in both French and Arabic languages, with options to filter by date range and bulletin type.

## Features

- **Multi-language support**: French (`fr`) and Arabic (`ar`)
- **Bulletin type filtering**: General, International, or All bulletins
- **Date range filtering**: Retrieve bulletins within specific date ranges
- **CSV export**: Download scraped data as CSV files
- **CORS enabled**: Ready for frontend integration
- **Robust date parsing**: Handles various date formats including pre-1970 dates

## Installation and Setup

### Prerequisites

- Python 3.7+
- pip package manager

### Dependencies

```bash
pip install fastapi httpx beautifulsoup4 uvicorn pydantic
```

### Running the Service

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 800
```

The API will be available at `http://0.0.0.0:8000`

## API Endpoints

### Base URL
```
http://localhost:8000
```

### 1. Root Endpoint

**GET** `/`

Returns basic API information.

**Response:**
```json
{
  "message": "Moroccan Official Gazette Scraper API"
}
```

### 2. Scrape Bulletins

**POST** `/scrape`

Scrapes bulletin data based on the provided parameters.

**Request Body:**
```json
{
  "start_date": "2024-01-01",
  "end_date": "2024-01-31",
  "language": "fr",
  "bulletin_type": "all"
}
```

**Parameters:**
- `start_date` (required): Start date in YYYY-MM-DD format
- `end_date` (required): End date in YYYY-MM-DD format
- `language` (optional): Language preference - `"fr"` (French) or `"ar"` (Arabic). Default: `"fr"`
- `bulletin_type` (optional): Type of bulletins - `"general"`, `"international"`, or `"all"`. Default: `"all"`

**Response:**
```json
{
  "success": true,
  "message": "Found 25 bulletins",
  "data": [
    {
      "BoNum": "7254",
      "BoDate": "2024-01-15",
      "BoUrl": "https://www.sgg.gov.ma/...",
      "BoType": "general"
    }
  ],
  "total_count": 25
}
```

**Response Fields:**
- `success`: Boolean indicating operation success
- `message`: Descriptive message about the operation
- `data`: Array of bulletin entries
- `total_count`: Total number of bulletins found

**Bulletin Entry Fields:**
- `BoNum`: Bulletin number
- `BoDate`: Publication date (YYYY-MM-DD format)
- `BoUrl`: Direct URL to the bulletin
- `BoType`: Type of bulletin ("general" or "international")

### 3. Download CSV

**POST** `/scrape/csv`

Scrapes bulletin data and returns it as a downloadable CSV file.

**Request Body:** Same as `/scrape` endpoint

**Response:** CSV file download with filename format: `bulletins_{language}_{start_date}_{end_date}.csv`

**CSV Structure:**
```csv
Type,Number,Date,URL
general,7254,2024-01-15,https://www.sgg.gov.ma/...
international,7255,2024-01-16,https://www.sgg.gov.ma/...
```



## Error Handling

The API returns appropriate HTTP status codes and error messages:

### Common Error Responses

**400 Bad Request:**
```json
{
  "detail": "Start date must be before end date"
}
```

**500 Internal Server Error:**
```json
{
  "success": false,
  "message": "Error: Connection timeout",
  "data": [],
  "total_count": 0
}
```

### Validation Errors

- Invalid date format
- Start date after end date
- Unsupported language code
- Invalid bulletin type

## Usage Examples

### Python Example

```python
import requests
import json

# Basic scraping request
url = "http://localhost:8000/scrape"
payload = {
    "start_date": "2024-01-01",
    "end_date": "2024-01-31",
    "language": "fr",
    "bulletin_type": "general"
}

response = requests.post(url, json=payload)
data = response.json()

print(f"Found {data['total_count']} bulletins")
for bulletin in data['data']:
    print(f"Bulletin {bulletin['BoNum']} - {bulletin['BoDate']}")
```

