# import requests

# url = "http://localhost:8000/upload"
# file_path = r"C:\Users\hp\Desktop\digex\invoice-data-extraction\backend\uploads\Report_Idriss_MIMOUDI_25 (1).pdf"

# with open(file_path, "rb") as f:
#     files = {"file": (file_path.split("\\")[-1], f, "application/pdf")}
#     response = requests.post(url, files=files)

# print("Status code:", response.status_code)
# print("Response JSON:", response.json())
import asyncio
from motor.motor_asyncio import AsyncIOMotorClient

async def main():
    MONGODB_URI = "mongodb://localhost:27017"
    client = AsyncIOMotorClient(MONGODB_URI)
    db = client.fileuploads

    cursor = db.fs.files.find({})
    files = await cursor.to_list(length=100)

    if not files:
        print("No files found in GridFS.")
    else:
        print("Uploaded files:")
        for f in files:
            print(f"ID: {f.get('_id')}, Filename: {f.get('filename')}, Upload Date: {f.get('metadata', {}).get('upload_date')}, Content-Type: {f.get('metadata', {}).get('content_type')}")

if __name__ == "__main__":
    asyncio.run(main())
