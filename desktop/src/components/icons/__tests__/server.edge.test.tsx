import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { Icon } from '../Icon';

describe('Icon — server (stage-4) + all icons valid', () => {
  it('renders server icon without errors', () => {
    const { container } = render(<Icon name="server" size={24} />);
    const svg = container.querySelector('svg');
    expect(svg).toBeDefined();
    expect(svg?.getAttribute('width')).toBe('24');
    expect(svg?.getAttribute('height')).toBe('24');
    expect(svg?.getAttribute('viewBox')).toBe('0 0 24 24');
  });

  it('server icon has path data', () => {
    const { container } = render(<Icon name="server" size={24} />);
    const path = container.querySelector('path');
    expect(path).toBeDefined();
    expect(path?.getAttribute('d')?.length).toBeGreaterThan(10);
  });

  it('renders all new stage-4 icons', () => {
    const stage4Icons = ['server', 'file-text'];
    for (const name of stage4Icons) {
      const { container } = render(<Icon name={name} size={20} />);
      expect(container.querySelector('svg')).toBeDefined();
      expect(container.querySelector('path')).toBeDefined();
    }
  });

  it('renders all icons used in CONFIG_SECTIONS', () => {
    const configIcons = ['settings', 'cpu', 'server', 'file-text', 'puzzle', 'book', 'search', 'refresh', 'download'];
    for (const name of configIcons) {
      const { container } = render(<Icon name={name} size={18} />);
      const svg = container.querySelector('svg');
      expect(svg, `Icon "${name}" should render an SVG`).toBeDefined();
      const path = container.querySelector('path');
      expect(path, `Icon "${name}" should have a path`).toBeDefined();
    }
  });

  it('server icon handles different sizes', () => {
    for (const size of [12, 16, 20, 24, 48]) {
      const { container } = render(<Icon name="server" size={size} />);
      const svg = container.querySelector('svg');
      expect(svg?.getAttribute('width')).toBe(String(size));
      expect(svg?.getAttribute('height')).toBe(String(size));
    }
  });

  it('server icon uses currentColor stroke', () => {
    const { container } = render(<Icon name="server" size={24} />);
    const svg = container.querySelector('svg');
    expect(svg?.getAttribute('stroke')).toBe('currentColor');
  });
});
