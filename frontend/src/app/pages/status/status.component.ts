import { Component, OnDestroy, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { FormsModule } from '@angular/forms';
import { Router } from '@angular/router';
import { firstValueFrom } from 'rxjs';
import { ApiService } from '../../services/api.service';

type TaskRow = {
  task_id: string | null;
  file_id: string;
  filename?: string;
  baseName?: string;
  status: string;
  created_at?: string;
  updated_at?: string;
  canValidate: boolean;
};

@Component({
  standalone: true,
  selector: 'status-page',
  imports: [CommonModule, FormsModule],
  templateUrl: './status.component.html',
  styleUrl: './status.component.css',
})
export class StatusPage implements OnInit, OnDestroy {
  rows: TaskRow[] = [];
  filtered: TaskRow[] = [];
  query = '';
  filterStatus = '';
  pollingId?: any;

  private deleting = new Set<string>();

  constructor(private api: ApiService, private router: Router) {}

  ngOnInit(): void {
    this.load();
    this.pollingId = setInterval(() => this.load(true), 5000);
  }
  ngOnDestroy(): void { if (this.pollingId) clearInterval(this.pollingId); }

  async load(_silent = false) {
    try {
      const list: any = await firstValueFrom(this.api.listTasks(200));
      this.rows = (list?.tasks ?? []).map((t: any) => {
        const filename: string = t.filename ?? '';
        const baseName: string = filename ? filename.replace(/\.[^.]+$/, '') : (t.file_id ?? '');
        const status: string = t.status || 'unknown';
        return {
          task_id: t.task_id ?? null,
          file_id: t.file_id,
          filename,
          baseName,
          status,
          created_at: t.created_at,
          updated_at: t.updated_at,
          canValidate: status === 'done',
        };
      });
    } catch {
      const filesRes: any = await firstValueFrom(this.api.listFiles());
      const files = filesRes?.files ?? filesRes ?? [];
      this.rows = (files as any[]).map((f: any) => {
        const filenameFull = f.filename || '';
        const baseName = filenameFull ? filenameFull.replace(/\.[^.]+$/, '') : (f.id ?? '');
        const processed = !!(f.processed ?? f.metadata?.processed);
        return {
          task_id: null,
          file_id: f.id,
          filename: filenameFull,
          baseName,
          status: processed ? 'done' : 'queued',
          created_at: f.upload_date,
          updated_at: processed ? f.processed_at : f.upload_date,
          canValidate: processed,
        } as TaskRow;
      });
    } finally {
      this.applyFilters();
    }
  }

  refresh() { this.load(true); }

  applyFilters() {
    const q = (this.query || '').toLowerCase().trim();
    const s = (this.filterStatus || '').toLowerCase().trim();
    this.filtered = (this.rows || []).filter((r) => {
      const name = (r.baseName || r.filename || r.file_id || '').toLowerCase();
      const okQ = !q || name.includes(q);
      const okS = !s || (r.status || '').toLowerCase() === s;
      return okQ && okS;
    });
  }

  onValidate(row: TaskRow) {
    const taskId = row.task_id ?? `file-${row.file_id}`;
    this.router.navigate(['/validate', taskId, row.file_id],
    {
      // either way works; I show both. Pick one and keep it consistent.
      queryParams: { drawer: 'off' },            // OPTION A: query param
      state: { drawerOpen: false }               // OPTION B: navigation state
    });
  }

  showDeleteFor(row: TaskRow) {
    return true; 
  }
  isBusy(row: TaskRow) {
    const k = (row.status || '').toLowerCase();
    return k === 'extracting' || k === 'structuring';
  }
  isDeleting(row: TaskRow) {
    return this.deleting.has(row.file_id);
  }

  async onDelete(row: TaskRow) {
    if (this.isBusy(row)) return; 
    const name = row.baseName || row.filename || row.file_id;
    const ok = confirm(`Supprimer définitivement "${name}" ?\nCette action supprimera le fichier et les données associées.`);
    if (!ok) return;

    try {
      this.deleting.add(row.file_id);
      await firstValueFrom(this.api.deleteFile(row.file_id));
      await this.load(true);
    } catch (e) {
      console.error(e);
      alert('Échec de la suppression.');
    } finally {
      this.deleting.delete(row.file_id);
    }
  }

  badgeClass(s: string) {
    const k = (s || '').toLowerCase();
    switch (k) {
      case 'queued': return 'badge badge--queued';
      case 'extracting': return 'badge badge--extracting';
      case 'structuring': return 'badge badge--structuring';
      case 'done': return 'badge badge--done';
      case 'validated': return 'badge badge--validated';   
      case 'error': return 'badge badge--error';
      default: return 'badge';
    }
  }
    
}
