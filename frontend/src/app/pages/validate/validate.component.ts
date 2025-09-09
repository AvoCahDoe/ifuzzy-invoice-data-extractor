import { Component, OnInit } from '@angular/core';
import { CommonModule } from '@angular/common';
import { ActivatedRoute, Router } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { ApiService } from '../../services/api.service';
import { DomSanitizer, SafeResourceUrl } from '@angular/platform-browser';

@Component({
  standalone: true,
  selector: 'validate-page',
  imports: [CommonModule, FormsModule],
  templateUrl: './validate.component.html',
  styleUrl: './validate.component.css',
})
export class ValidatePage implements OnInit {
  taskId = '';
  fileId = '';

  previewUrl: SafeResourceUrl | null = null;  
  isPdf = false;                               
  isBrowser = typeof window !== 'undefined';   
  drawerOpen = true;                            
  loading = false;

  extracted: any = {
    document_type: '',
    currency: '',
    payment_method: '',
    invoice_number: '',
    invoice_date: '',
    due_date: '',
    total_amount: '',
    tax_amount: '',
    line_items: [] as any[],
  };

  constructor(
    private route: ActivatedRoute,
    private api: ApiService,
    private sanitizer: DomSanitizer,
    private router: Router
  ) {}

  ngOnInit(): void {
    this.route.paramMap.subscribe(async (pm) => {
      const t = pm?.get('taskId') ?? '';
      const f = pm?.get('fileId') ?? '';
      if (!f) return;

      this.taskId = t;
      this.fileId = f;

      const rawUrl = this.api.fileRawUrl(this.fileId);
      this.isPdf = /\.pdf($|\?)/i.test(rawUrl);
      this.previewUrl = this.sanitizer.bypassSecurityTrustResourceUrl(rawUrl);

      this.loading = true;
      try {
        let data: any | null = null;
        try {
          const res = await this.api.getTaskData(this.taskId).toPromise();
          data = res?.data ?? null;
        } catch {}
        if (!data) {
          const ext = await this.api.getExtraction(this.fileId).toPromise();
          data = ext?.extraction?.extraction_data ?? null;
        }
        if (data) this.extracted = data;
      } finally {
        this.loading = false;
      }
    });
  }

  async confirm() {
    try {
      await this.api.updateStructured(this.fileId, this.extracted).toPromise();
      alert('Informations confirmées ✔');
      this.router.navigate(['/status']);
    } catch (e) {
      console.error(e);
      alert('Échec de la confirmation');
    }
  }

  trackByIndex = (i: number) => i;
}
