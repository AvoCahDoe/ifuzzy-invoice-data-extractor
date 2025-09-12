import {
  Component,
  OnInit,
  Inject,
  PLATFORM_ID,
  NgZone,
  ChangeDetectorRef,
  ApplicationRef,
} from '@angular/core';
import { CommonModule, isPlatformBrowser } from '@angular/common';
import { ActivatedRoute, Router } from '@angular/router';
import { FormsModule } from '@angular/forms';
import { firstValueFrom } from 'rxjs';
import { ApiService } from '../../services/api.service';
import { DomSanitizer, SafeResourceUrl, SafeUrl } from '@angular/platform-browser';

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

  previewPdfUrl: SafeResourceUrl | null = null; 
  previewImgUrl: SafeUrl | null = null;         
  isPdf = false;
  isBrowser = typeof window !== 'undefined';
  drawerOpen = false;

  dataLoading = false;
  previewLoading = false;

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
    private router: Router,
    @Inject(PLATFORM_ID) private platformId: Object,
    private appRef: ApplicationRef,  
    private zone: NgZone,
    private cdr: ChangeDetectorRef
  ) {}

  ngOnInit(): void {
    
    this.route.paramMap.subscribe(async (pm) => {
      const t = pm?.get('taskId') ?? '';
      const f = pm?.get('fileId') ?? '';
      if (!f) return;

      this.taskId = t;
      this.fileId = f;

      this.dataLoading = true;
      try {
        await this.loadAndPopulateData();
      } finally {
        this.dataLoading = false;
        this.cdr.markForCheck();
        this.cdr.detectChanges();
      }

      if (isPlatformBrowser(this.platformId)) {
        const rawUrl = this.api.fileRawUrl(this.fileId);
        const assumedPdf = /\.pdf($|\?)/i.test(rawUrl);

        this.previewLoading = true;
        try {
          await this.buildPreviewUrls(rawUrl, assumedPdf);
        } finally {
          this.previewLoading = false;
          this.cdr.markForCheck();
          this.cdr.detectChanges();
        }
      }
    });
  }

  private async loadAndPopulateData() {
    let data: any | null = null;

    try {
      const res = await firstValueFrom(this.api.getTaskData(this.taskId));
      data = res?.data ?? null;
    } catch { /* ignore */ }

    if (!data) {
      const ext = await firstValueFrom(this.api.getExtraction(this.fileId));
      data = ext?.extraction?.extraction_data ?? null;
    }

    this.zone.run(() => {
      if (data) {
        this.extracted = {
          ...this.extracted,
          ...data,
          line_items: Array.isArray(data.line_items) ? data.line_items : [],
        };
      }
      this.appRef.tick(); 
    });
  }

  private async buildPreviewUrls(rawUrl: string, assumedPdf: boolean) {
    try {
      const res = await fetch(rawUrl, { credentials: 'include' });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);

      const ct = (res.headers.get('Content-Type') || '').toLowerCase();
      const isRealPdf = ct.includes('application/pdf') || assumedPdf;
      const ab = await res.arrayBuffer();

      if (isRealPdf) {
        const blob = new Blob([ab], { type: 'application/pdf' });
        const objUrl = URL.createObjectURL(blob);
        const zoomed = objUrl + '#zoom=page-width'; 
        this.previewPdfUrl = this.sanitizer.bypassSecurityTrustResourceUrl(objUrl);
        this.previewImgUrl = null;
        this.isPdf = true;
      } else {
        const mime = ct && ct !== 'application/octet-stream' ? ct : 'image/*';
        const blob = new Blob([ab], { type: mime });
        const objUrl = URL.createObjectURL(blob);
        this.previewImgUrl = this.sanitizer.bypassSecurityTrustUrl(objUrl);
        this.previewPdfUrl = null;
        this.isPdf = false;
      }
    } catch {
      if (assumedPdf) {
        this.previewPdfUrl = this.sanitizer.bypassSecurityTrustResourceUrl(rawUrl);
        this.previewImgUrl = null;
        this.isPdf = true;
      } else {
        this.previewImgUrl = this.sanitizer.bypassSecurityTrustUrl(rawUrl);
        this.previewPdfUrl = null;
        this.isPdf = false;
      }
    }finally {
      this.zone.run(() => { 
          this.appRef.tick(); 
      });}
  }

  async confirm() {
    try {
      await firstValueFrom(this.api.updateStructured(this.fileId, this.extracted));
      alert('Informations confirmées ✔');
      this.router.navigate(['/status']);
    } catch (e) {
      console.error(e);
      alert('Échec de la confirmation');
    }
  }
  toggleDrawer() {
    this.drawerOpen = !this.drawerOpen;
    if (this.drawerOpen && typeof window !== 'undefined') {
      setTimeout(() => window.dispatchEvent(new Event('resize')), 50);
    }
  }


  trackByIndex = (i: number) => i;
}
