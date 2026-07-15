import { describe, it, expect } from 'vitest';
import { render, screen } from '@testing-library/react';
import { Icon } from '../Icon';
import { ICON_PATHS, getIconNames } from '../icons';

describe('Icon component', () => {
  it('renders an SVG element', () => {
    const { container } = render(<Icon name="home" />);
    const svg = container.querySelector('svg');
    expect(svg).toBeTruthy();
    expect(svg?.tagName.toLowerCase()).toBe('svg');
  });

  it('applies the specified size', () => {
    const { container } = render(<Icon name="home" size={32} />);
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('width')).toBe('32');
    expect(svg?.getAttribute('height')).toBe('32');
  });

  it('defaults to size 20', () => {
    const { container } = render(<Icon name="home" />);
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('width')).toBe('20');
    expect(svg?.getAttribute('height')).toBe('20');
  });

  it('uses viewBox 0 0 24 24', () => {
    const { container } = render(<Icon name="globe" />);
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('viewBox')).toBe('0 0 24 24');
  });

  it('renders stroke icons with fill="none" and stroke="currentColor"', () => {
    const { container } = render(<Icon name="home" />);
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('fill')).toBe('none');
    expect(svg?.getAttribute('stroke')).toBe('currentColor');
  });

  it('renders fill icons with fill="currentColor"', () => {
    const { container } = render(<Icon name="play" />);
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('fill')).toBe('currentColor');
    expect(svg?.getAttribute('stroke')).toBe('none');
  });

  it('adds title and aria-label when title prop is provided', () => {
    render(<Icon name="play" title="Start translation" />);
    const svg = screen.getByRole('img');
    expect(svg.getAttribute('aria-label')).toBe('Start translation');
    expect(svg.querySelector('title')?.textContent).toBe('Start translation');
  });

  it('is aria-hidden when no title is provided', () => {
    const { container } = render(<Icon name="home" />);
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('aria-hidden')).toBe('true');
  });

  it('returns null for unknown icon name', () => {
    const { container } = render(<Icon name="nonexistent-icon" />);
    expect(container.firstChild).toBeNull();
  });

  it('applies custom className', () => {
    const { container } = render(<Icon name="home" className="my-icon" />);
    const svg = container.querySelector('svg');
    expect(svg?.classList.contains('my-icon')).toBe(true);
  });
});

describe('ICON_PATHS registry', () => {
  it('contains all expected icon names', () => {
    const names = getIconNames();
    const expected = [
      'home', 'globe', 'settings', 'folder', 'folder-open', 'file-text',
      'database', 'book', 'library', 'user', 'cpu', 'loader', 'ban',
      'upload', 'download', 'x', 'play', 'square', 'check', 'check-circle',
      'alert-circle', 'puzzle', 'search', 'refresh', 'refresh-ccw',
      'workflow', 'inject', 'chevron-down', 'chevron-right', 'chevron-left',
      'chevron-up', 'plus', 'minus', 'trash', 'edit', 'copy', 'external-link',
    ];
    for (const name of expected) {
      expect(names).toContain(name);
    }
  });

  it('every icon has a non-empty path definition', () => {
    for (const [name, def] of Object.entries(ICON_PATHS)) {
      const paths = Array.isArray(def.d) ? def.d : [def.d];
      for (const p of paths) {
        expect(p.length).toBeGreaterThan(0);
      }
    }
  });

  it('fill icons are correctly marked', () => {
    expect(ICON_PATHS.play.fill).toBe(true);
    expect(ICON_PATHS.square.fill).toBe(true);
    expect(ICON_PATHS.home.fill).toBeUndefined();
    expect(ICON_PATHS.globe.fill).toBeUndefined();
  });
});
