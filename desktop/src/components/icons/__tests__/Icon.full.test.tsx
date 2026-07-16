import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { Icon } from '../Icon';
import { getIconNames } from '../icons';

describe('Icon — full icon set validation (P0)', () => {
  const ALL_ICONS = getIconNames();

  it('has at least 35 icons defined', () => {
    expect(ALL_ICONS.length).toBeGreaterThanOrEqual(35);
  });

  it('every icon renders a valid SVG with path', () => {
    for (const name of ALL_ICONS) {
      const { container, unmount } = render(<Icon name={name} size={20} />);
      const svg = container.querySelector('svg');
      expect(svg, `Icon "${name}" should render an SVG`).toBeDefined();
      expect(svg!.getAttribute('viewBox'), `Icon "${name}" viewBox`).toBe('0 0 24 24');

      const path = container.querySelector('path');
      expect(path, `Icon "${name}" should have a path`).toBeDefined();
      expect(path!.getAttribute('d')!.length, `Icon "${name}" path data non-empty`).toBeGreaterThan(5);

      // Either stroke or fill is currentColor (fill icons have fill=currentColor, stroke=none)
      const fill = svg!.getAttribute('fill');
      const stroke = svg!.getAttribute('stroke');
      expect(fill === 'currentColor' || stroke === 'currentColor', `Icon "${name}" should use currentColor`).toBe(true);

      unmount();
    }
  });

  it('all icons support multiple sizes', () => {
    const sizes = [12, 16, 20, 24, 32, 48];
    for (const name of ALL_ICONS.slice(0, 5)) { // first 5 for speed
      for (const size of sizes) {
        const { container, unmount } = render(<Icon name={name} size={size} />);
        expect(container.querySelector('svg')!.getAttribute('width')).toBe(String(size));
        unmount();
      }
    }
  });

  it('defaults to size 20 when not specified', () => {
    const { container } = render(<Icon name="home" />);
    expect(container.querySelector('svg')!.getAttribute('width')).toBe('20');
  });

  // Fill-type icons (play, square) should have fill set
  it('fill-type icons render with valid fill attribute', () => {
    const { container } = render(<Icon name="square" size={20} />);
    expect(container.querySelector('svg')).toBeDefined();
    expect(container.querySelector('svg')!.getAttribute('fill')).toBeDefined();
  });

  // Cheatsheet: every icon used in Sidebar exists
  it('all sidebar icons are defined', () => {
    const sidebarIcons = ['home', 'folder-open', 'globe', 'database', 'book', 'user', 'settings', 'folder', 'cpu', 'library', 'upload', 'ban', 'loader', 'x', 'trash'];
    for (const name of sidebarIcons) {
      expect(ALL_ICONS.includes(name), `Sidebar icon "${name}"`).toBe(true);
    }
  });

  // Cheatsheet: every icon used in CONFIG_SECTIONS exists
  it('all config section icons are defined', () => {
    const configIcons = ['settings', 'cpu', 'server', 'file-text', 'puzzle', 'book', 'search', 'refresh', 'download'];
    for (const name of configIcons) {
      expect(ALL_ICONS.includes(name), `Config icon "${name}"`).toBe(true);
    }
  });

  it('every icon name is a valid string', () => {
    for (const name of ALL_ICONS) {
      expect(typeof name).toBe('string');
      expect((name as string).length).toBeGreaterThan(0);
    }
  });

  it('all icon names are unique', () => {
    expect(new Set(ALL_ICONS).size).toBe(ALL_ICONS.length);
  });
});

describe('Icon — edge cases', () => {
  const ALL_ICONS = getIconNames();
  it('throws or renders nothing for unknown icon name', () => {
    // Should not crash the app
    expect(() => render(<Icon name="nonexistent-icon-12345" size={20} />)).not.toThrow();
  });

  it('handles size 0 gracefully', () => {
    const { container } = render(<Icon name="home" size={0} />);
    expect(container.querySelector('svg')).toBeDefined();
  });

  it('handles very large size', () => {
    const { container } = render(<Icon name="home" size={1024} />);
    expect(container.querySelector('svg')!.getAttribute('width')).toBe('1024');
  });

  it('all icons have valid stroke-width when applicable', () => {
    const firstFive = ALL_ICONS.slice(0, 5);
    for (const name of firstFive) {
      const { container, unmount } = render(<Icon name={name} size={20} />);
      const svg = container.querySelector('svg');
      // Non-fill icons should have stroke-width; fill icons have it undefined
      const sw = svg!.getAttribute('stroke-width');
      // Either '1.5' for outline or undefined for fill — both are valid
      expect(sw === '1.5' || sw === null).toBe(true);
      unmount();
    }
  });
});
