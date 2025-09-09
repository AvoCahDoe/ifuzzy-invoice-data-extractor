import { Injectable } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable, of, throwError } from 'rxjs';
import { catchError, map } from 'rxjs/operators';

@Injectable({ providedIn: 'root' })
export class ApiService {
  private base = 'http://127.0.0.1:8000';

  constructor(private http: HttpClient) {}

  upload(file: File): Observable<{ file_id: string }> {
    const form = new FormData();
    form.append('file', file);
    return this.http.post<{ file_id: string }>(`${this.base}/upload`, form);
  }

  process(fileId: string, force_ocr = false): Observable<any> {
    const params = new HttpParams().set('force_ocr', String(force_ocr));
    return this.http.post(`${this.base}/process/${fileId}`, {}, { params });
  }

  structure(fileId: string): Observable<any> {
    return this.http.post(`${this.base}/structure/${fileId}`, {});
  }

  getExtraction(fileId: string): Observable<any> {
    return this.http.get(`${this.base}/extraction/${fileId}`);
  }

  updateStructured(fileId: string, data: any): Observable<any> {
    return this.http.put(`${this.base}/update/${fileId}`, data);
  }

  listFiles(): Observable<any[]> {
    return this.http.get<{ files: any[] }>(`${this.base}/files`).pipe(
      map(r => r?.files ?? [])
    );
  }

  sendTask(fileId: string, force_ocr = false, structure_after = true): Observable<{ task_id: string }> {
    return this.http.post<{ task_id: string }>(`${this.base}/task/send`, { file_id: fileId, force_ocr, structure_after });
  }

  listTasks(limit = 100): Observable<any> {
    const params = new HttpParams().set('limit', String(limit));
    return this.http.get(`${this.base}/task/list`, { params });
  }

  getTaskState(taskId: string): Observable<any> {
    return this.http.get(`${this.base}/task/state/${taskId}`);
  }

  getTaskData(taskId: string): Observable<any> {
    return this.http.get(`${this.base}/task/data/${taskId}`);
  }

  fileRawUrl(fileId: string) {
    return `${this.base}/files/raw/${fileId}`;
  }

  deleteFile(fileId: string) {
  return this.http.delete(`${this.base}/files/${fileId}`);
  }

  validateTask(taskId: string) {
    return this.http.post(`${this.base}/task/validate/${taskId}`, {});
  }

}
