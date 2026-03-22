import { Component, Input, Output, EventEmitter } from '@angular/core';
import { CommonModule } from '@angular/common';
import type { SafeUrl } from '@angular/platform-browser';

/**
 * Reusable zoomable image viewer.
 * Handles zoom state, controls, and scrollable view.
 * Use mode="inline" for preview, mode="modal" for fullscreen overlay.
 */
@Component({
  standalone: true,
  selector: 'zoomable-image-viewer',
  imports: [CommonModule],
  templateUrl: './zoomable-image-viewer.component.html',
  styleUrl: './zoomable-image-viewer.component.css',
})
export class ZoomableImageViewerComponent {
  @Input() imageUrl: string | SafeUrl | null = null;
  @Input() alt = 'Image';
  @Input() mode: 'inline' | 'modal' = 'inline';
  @Input() showClose = false;

  @Output() close = new EventEmitter<void>();

  zoom = 1;
  readonly ZOOM_MIN = 0.5;
  readonly ZOOM_MAX = 3;
  readonly ZOOM_STEP = 0.25;

  zoomIn() {
    this.zoom = Math.min(this.ZOOM_MAX, this.zoom + this.ZOOM_STEP);
  }

  zoomOut() {
    this.zoom = Math.max(this.ZOOM_MIN, this.zoom - this.ZOOM_STEP);
  }

  zoomReset() {
    this.zoom = 1;
  }

  onClose() {
    this.close.emit();
  }

  onWheel(event: WheelEvent) {
    if (event.ctrlKey || event.metaKey) {
      event.preventDefault();
      event.stopPropagation();
      // Smooth zoom: larger deltaY = bigger step
      const delta = Math.sign(event.deltaY);
      if (delta < 0) this.zoomIn();
      else this.zoomOut();
    }
  }
}
