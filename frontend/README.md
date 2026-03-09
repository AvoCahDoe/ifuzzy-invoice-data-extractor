# 💻 Invoice Data Extractor Frontend

The frontend for the Invoice Data Extractor is a modern, high-performance web application built with **Angular 20** and **Angular SSR (Server-Side Rendering)**. It provides an intuitive interface for managing the entire invoice extraction lifecycle.

## 🚀 Key Features

- **Drag-and-Drop Upload**: Simple interface for uploading single or batch invoices.
- **Real-time Status Tracking**: Monitor extraction tasks through queued, extracting, and structuring states.
- **Side-by-Side Visualization**: View the original invoice image alongside the extracted Markdown and structured JSON.
- **Interactive Correction**: Built-in editor to manually validate and correct extracted fields.
- **Confidence Scoring**: Visual indicators for extraction quality (Scoring: Visual OCR + LLM Semantic + Mathematical Logic).
- **Responsive Design**: Optimized for both desktop and mobile viewing.

## 🛠️ Tech Stack

- **Framework**: Angular v20
- **Rendering**: Angular SSR (Server-Side Rendering) for fast initial loads.
- **Styling**: Tailwind CSS (if applicable) / Custom CSS.
- **State Management**: Reactive services with RxJS.

## 🚀 Getting Started

### Prerequisites

- [Node.js](https://nodejs.org/) (LTS recommended)
- [Angular CLI](https://angular.dev/tools/cli)

### Installation

```bash
npm install
```

### Development server

Run `npm start` (or `ng serve`) for a local dev server. Navigate to `http://localhost:4200/`. The app will automatically reload on changes.

### Production Build

```bash
npm run build
```

This compiles the project and optimizes it for performance, storing build artifacts in the `dist/` directory.

## 🏗️ Architecture

- **`src/app/pages`**: Contains the main views (Upload, Status, Results).
- **`src/app/services`**: Handles API communication with the Backend Orchestrator.
- **`src/app/components`**: Reusable UI elements for data visualization and editing.

## 🏁 Configuration

The frontend connects to the backend API via the `API_BASE_URL` environment variable (configured for Docker) or defaults to `http://localhost:8001` in development.
