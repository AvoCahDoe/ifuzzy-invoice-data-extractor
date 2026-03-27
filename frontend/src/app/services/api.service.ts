import { Injectable } from '@angular/core';
import { HttpClient, HttpParams } from '@angular/common/http';
import { Observable } from 'rxjs';
import { map } from 'rxjs/operators';

@Injectable({ providedIn: 'root' })
export class ApiService {
  private base = 'http://backend:8001';

  constructor(private http: HttpClient) {
    const isBrowser = typeof window !== 'undefined';
    if (isBrowser) {
      // Same-origin `/api` is proxied by Express (production) or `ng serve` (proxy.conf.json).
      this.base = '/api';
    } else {
      this.base =
        (globalThis as any)?.process?.env?.API_BASE_URL ?? 'http://backend:8001';
    }
  }

  upload(file: File): Observable<{ file_id: string }> {
    const form = new FormData();
    form.append('file', file);
    return this.http.post<{ file_id: string }>(`${this.base}/upload`, form);
  }

  getExtraction(fileId: string): Observable<any> {
    return this.http.get(`${this.base}/extraction/${fileId}`);
  }

  updateStructured(fileId: string, data: any, taskId?: string): Observable<any> {
    const options = taskId ? { params: { task_id: taskId } } : {};
    return this.http.put(`${this.base}/update/${fileId}`, data, options);
  }

  listFiles(): Observable<any[]> {
    return this.http.get<{ files: any[] }>(`${this.base}/files`).pipe(
      map(r => r?.files ?? [])
    );
  }

  sendTask(
    fileId: string,
    force_ocr = false,
    structure_after = true,
    engine = 'rapidocr'
  ): Observable<{ task_id: string }> {
    return this.http.post<{ task_id: string }>(`${this.base}/task/send`, { 
      file_id: fileId, 
      force_ocr, 
      do_structure: structure_after,
      engine
    });
  }

  listTasks(limit = 100): Observable<any> {
    const params = new HttpParams().set('limit', String(limit));
    return this.http.get(`${this.base}/task/list`, { params });
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

  getFileBlob(fileId: string) {
    const url = this.fileRawUrl(fileId);
    return this.http.get(url, { responseType: 'blob' });
  }

  cleanupDatabase(): Observable<any> {
    return this.http.post(`${this.base}/system/cleanup`, {});
  }
}
