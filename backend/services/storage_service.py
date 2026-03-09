import os
from pymongo import MongoClient
import gridfs
from datetime import datetime

class StorageService:
    def __init__(self, mongo_uri: str = None):
        self.mongo_uri = mongo_uri or os.getenv("MONGODB_URI", "mongodb://mongodb:27017")
        self.client = MongoClient(self.mongo_uri)
        self.db = self.client.invoice_db
        self.files_col = self.db.files
        self.tasks_col = self.db.tasks
        self.structured_col = self.db.structured_data
        self.fs = gridfs.GridFS(self.db)

    def get_file(self, file_id: str):
        if not self.fs.exists(_id=file_id):
            return None
        return self.fs.get(file_id)

    def put_file(self, content: bytes, filename: str, file_id: str):
        return self.fs.put(content, filename=filename, _id=file_id)

    def update_task_status(self, task_id: str, status: str, error: str = None, engine: str = None):
        update = {"status": status, "updated_at": datetime.utcnow().isoformat()}
        if error:
            update["error"] = error
        if engine:
            update["engine"] = engine
        self.tasks_col.update_one({"_id": task_id}, {"$set": update})

    def save_structured_data(self, task_id: str, file_id: str, structured_data: dict, metadata: dict = None):
        doc = {
            "task_id": task_id,
            "file_id": file_id,
            "data": structured_data,
            "metadata": metadata or {},
            "created_at": datetime.utcnow().isoformat()
        }
        # Update if exists, else insert (unique by task_id)
        self.structured_col.update_one(
            {"task_id": task_id},
            {"$set": doc},
            upsert=True
        )

    def get_task(self, task_id: str):
        return self.tasks_col.find_one({"_id": task_id})

    def insert_task(self, task_doc: dict):
        return self.tasks_col.insert_one(task_doc)

storage_service = StorageService()
