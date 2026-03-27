import {
  Component,
  Input,
  Output,
  EventEmitter,
  ChangeDetectorRef,
  ViewChild,
  ElementRef,
} from '@angular/core';
import { CommonModule } from '@angular/common';
import type { SafeUrl } from '@angular/platform-browser';

/** One OCR block from RapidOCR / backend extractions.result.blocks */
export interface OcrBlock {
  text: string;
  bbox: [number, number, number, number];
  confidence: number;
  page_num?: number;
}

/** Table region from layout / PyMuPDF */
export interface TableRegion {
  kind?: string;
  page_num?: number;
  bbox: [number, number, number, number];
}

/** Per-role anchor from fuzzy rule_extractor */
export interface AnchorRoleInfo {
  detected?: boolean;
  bbox?: number[] | null;
}

export interface AnchorIndicators {
  vendor?: AnchorRoleInfo;
  customer?: AnchorRoleInfo;
  payment?: AnchorRoleInfo;
  line_item_header?: AnchorRoleInfo;
}

export interface BboxBoxLayout {
  left: number;
  top: number;
  width: number;
  height: number;
  cls: 'high' | 'mid' | 'low';
  text: string;
  confidence: number;
}

export interface ExtraBoxLayout {
  kind: 'table' | 'anchor';
  left: number;
  top: number;
  width: number;
  height: number;
  anchorRole?: 'vendor' | 'customer' | 'payment' | 'line_item_header';
  tooltip: string;
}

/**
 * Image viewer with OCR, table, and anchor bounding-box overlays.
 * Isolated: consumes plain bbox arrays from the backend.
 */
@Component({
  standalone: true,
  selector: 'bbox-overlay-viewer',
  imports: [CommonModule],
  templateUrl: './bbox-overlay-viewer.component.html',
  styleUrl: './bbox-overlay-viewer.component.css',
})
export class BboxOverlayViewerComponent {
  @ViewChild('coverImg') coverImg?: ElementRef<HTMLImageElement>;

  @Input() imageUrl: string | SafeUrl | null = null;
  @Input() alt = 'Document';
  @Input() mode: 'inline' | 'modal' = 'modal';
  @Input() showClose = false;

  @Input() set blocks(value: OcrBlock[] | null | undefined) {
    this._blocks = Array.isArray(value) ? value : [];
    requestAnimationFrame(() => this.applyLayout());
  }
  get blocks(): OcrBlock[] {
    return this._blocks;
  }

  @Input() set tableRegions(value: TableRegion[] | null | undefined) {
    this._tableRegions = Array.isArray(value) ? value : [];
    requestAnimationFrame(() => this.applyLayout());
  }
  get tableRegions(): TableRegion[] {
    return this._tableRegions;
  }

  @Input() set anchorIndicators(value: AnchorIndicators | null | undefined) {
    this._anchorIndicators = value && typeof value === 'object' ? value : null;
    requestAnimationFrame(() => this.applyLayout());
  }
  get anchorIndicators(): AnchorIndicators | null {
    return this._anchorIndicators;
  }

  @Output() close = new EventEmitter<void>();

  zoom = 1;
  readonly ZOOM_MIN = 0.5;
  readonly ZOOM_MAX = 3;
  readonly ZOOM_STEP = 0.25;

  showBoxes = true;

  boxLayouts: BboxBoxLayout[] = [];
  extraLayouts: ExtraBoxLayout[] = [];

  private _blocks: OcrBlock[] = [];
  private _tableRegions: TableRegion[] = [];
  private _anchorIndicators: AnchorIndicators | null = null;
  private naturalW = 0;
  private naturalH = 0;

  constructor(private cdr: ChangeDetectorRef) {}

  onImageLoad(ev: Event) {
    const img = ev.target as HTMLImageElement;
    this.naturalW = img.naturalWidth || 1;
    this.naturalH = img.naturalHeight || 1;
    requestAnimationFrame(() => this.applyLayout(img));
  }

  zoomIn() {
    this.zoom = Math.min(this.ZOOM_MAX, this.zoom + this.ZOOM_STEP);
    requestAnimationFrame(() =>
      this.applyLayout(this.coverImg?.nativeElement),
    );
  }

  zoomOut() {
    this.zoom = Math.max(this.ZOOM_MIN, this.zoom - this.ZOOM_STEP);
    requestAnimationFrame(() =>
      this.applyLayout(this.coverImg?.nativeElement),
    );
  }

  zoomReset() {
    this.zoom = 1;
    requestAnimationFrame(() =>
      this.applyLayout(this.coverImg?.nativeElement),
    );
  }

  onClose() {
    this.close.emit();
  }

  onWheel(event: WheelEvent) {
    if (event.ctrlKey || event.metaKey) {
      event.preventDefault();
      event.stopPropagation();
      const delta = Math.sign(event.deltaY);
      if (delta < 0) this.zoomIn();
      else this.zoomOut();
    }
  }

  toggleBoxes() {
    this.showBoxes = !this.showBoxes;
  }

  formatTooltip(box: BboxBoxLayout): string {
    const pct = Math.round(box.confidence * 100);
    const t = box.text || '(empty)';
    return `${t} · ${pct}%`;
  }

  get hasAnyOverlay(): boolean {
    return (
      this.boxLayouts.length > 0 ||
      this.extraLayouts.length > 0
    );
  }

  private visibleBlocks(): OcrBlock[] {
    return this._blocks.filter(
      (b) => b.page_num === undefined || b.page_num === 0,
    );
  }

  private visibleTableRegions(): TableRegion[] {
    return this._tableRegions.filter(
      (r) => r.page_num === undefined || r.page_num === 0,
    );
  }

  private scaleBox(
    bb: number[],
    sx: number,
    sy: number,
  ): { left: number; top: number; width: number; height: number } | null {
    if (!Array.isArray(bb) || bb.length < 4) return null;
    const [x0, y0, x1, y1] = bb.map(Number);
    if ([x0, y0, x1, y1].some((n) => Number.isNaN(n))) return null;
    return {
      left: Math.min(x0, x1) * sx,
      top: Math.min(y0, y1) * sy,
      width: Math.abs(x1 - x0) * sx,
      height: Math.abs(y1 - y0) * sy,
    };
  }

  private applyLayout(img?: HTMLImageElement | null) {
    const el = img ?? this.coverImg?.nativeElement;
    if (!el || !this.naturalW || !this.naturalH) {
      this.boxLayouts = [];
      this.extraLayouts = [];
      this.cdr.markForCheck();
      return;
    }

    const cw = el.clientWidth;
    const ch = el.clientHeight;
    const sx = cw / this.naturalW;
    const sy = ch / this.naturalH;

    const out: BboxBoxLayout[] = [];
    for (const b of this.visibleBlocks()) {
      const bb = b.bbox;
      if (!Array.isArray(bb) || bb.length < 4) continue;
      const [x0, y0, x1, y1] = bb.map(Number);
      if ([x0, y0, x1, y1].some((n) => Number.isNaN(n))) continue;

      const left = Math.min(x0, x1) * sx;
      const top = Math.min(y0, y1) * sy;
      const width = Math.abs(x1 - x0) * sx;
      const height = Math.abs(y1 - y0) * sy;
      const c = typeof b.confidence === 'number' ? b.confidence : 0;
      let cls: 'high' | 'mid' | 'low' = 'low';
      if (c >= 0.85) cls = 'high';
      else if (c >= 0.6) cls = 'mid';

      out.push({
        left,
        top,
        width,
        height,
        cls,
        text: (b.text ?? '').slice(0, 200),
        confidence: c,
      });
    }
    this.boxLayouts = out;

    const extras: ExtraBoxLayout[] = [];

    for (const tr of this.visibleTableRegions()) {
      const geom = this.scaleBox(tr.bbox as number[], sx, sy);
      if (!geom) continue;
      extras.push({
        kind: 'table',
        ...geom,
        tooltip: 'Table region (layout / PDF)',
      });
    }

    const ai = this._anchorIndicators;
    if (ai) {
      const roles: Array<{
        key: 'vendor' | 'customer' | 'payment' | 'line_item_header';
        label: string;
      }> = [
        { key: 'vendor', label: 'Vendor anchor' },
        { key: 'customer', label: 'Customer anchor' },
        { key: 'payment', label: 'Payment anchor' },
        { key: 'line_item_header', label: 'Line-item header anchor' },
      ];
      for (const { key, label } of roles) {
        const entry = ai[key];
        if (!entry?.detected || !entry.bbox) continue;
        const geom = this.scaleBox(entry.bbox, sx, sy);
        if (!geom) continue;
        extras.push({
          kind: 'anchor',
          anchorRole: key,
          ...geom,
          tooltip: label,
        });
      }
    }

    this.extraLayouts = extras;
    this.cdr.markForCheck();
  }
}
