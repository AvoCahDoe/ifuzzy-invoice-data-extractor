# Invoice Data Extraction (Angular + SSR)

This project is a full-stack invoice data extraction tool with a frontend built using Angular Standalone with Server-Side Rendering (SSR).
It allows users to upload invoices in PDF or image format (PNG/JPEG), sends them to a backend extraction service, and displays the extracted fields.

## Features

- File Upload Interface: Upload invoices in `.pdf`, `.png`, `.jpeg`, or `.jpg` format.
- Automatic Field Extraction: Extracts key invoice details such as:
  - Document Type
  - Currency
  - Payment Method
  - Invoice Number
  - Invoice Date
  - Due Date
  - Total Amount
  - Tax Amount
- Responsive UI with simple styling for quick deployment.
- Server-Side Rendering (SSR) for better SEO and initial load performance.

## Tech Stack

### Frontend
- Angular (Standalone components)
- Angular Universal for SSR
- HTML5, CSS3

### Backend
- Connects to a FastAPI or other API service for invoice data extraction.

## Getting Started

### 1. Install Dependencies
```bash
npm install
```

### 2. Development Server
Run the application in browser mode:
```bash
npm run dev
```
Or with SSR enabled:
```bash
npm run dev:ssr
```

### 3. Build
```bash
# Build browser bundle
npm run build

# Build with SSR
npm run build:ssr
```

### 4. Run Production SSR Server
```bash
npm run serve:ssr
```

## Configuration

The frontend is configured to connect to a backend extraction API.
You can update API URLs or environment variables in your Angular service files or environment config.

## Screenshots

Invoice Upload Form
```
[User uploads an invoice via form]
```

Extracted Fields Display
```
Document Type: Invoice
Currency: USD
...
```

## License
This project is licensed under the MIT License — see the LICENSE file for details.

## Contributing
Pull requests are welcome. For major changes, please open an issue first to discuss your ideas.

## Author
Developed by Baka Mohamed
Contact: bakamoohamed@gmail.com
