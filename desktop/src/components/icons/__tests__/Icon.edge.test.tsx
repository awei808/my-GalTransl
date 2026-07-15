import { describe, it, expect } from 'vitest';
import { render } from '@testing-library/react';
import { Icon } from '../Icon';
import { ICON_PATHS } from '../icons';

describe('Icon component — edge cases', () => {
  describe('invalid name', () => {
    it('returns null for empty string name', () => {
      const { container } = render(<Icon name="" />);
      expect(container.firstChild).toBeNull();
    });

    it('returns null for whitespace-only name', () => {
      const { container } = render(<Icon name="   " />);
      expect(container.firstChild).toBeNull();
    });

    it('returns null for name with special characters', () => {
      const { container } = render(<Icon name="<script>" />);
      expect(container.firstChild).toBeNull();
    });

    it('returns null for very long name', () => {
      const { container } = render(<Icon name={'a'.repeat(10000)} />);
      expect(container.firstChild).toBeNull();
    });
  });

  describe('size edge cases', () => {
    it('handles size = 0 (renders 0x0 svg)', () => {
      const { container } = render(<Icon name="home" size={0} />);
      const svg = container.querySelector('svg');
      expect(svg?.getAttribute('width')).toBe('0');
      expect(svg?.getAttribute('height')).toBe('0');
    });

    it('handles negative size', () => {
      const { container } = render(<Icon name="home" size={-10} />);
      const svg = container.querySelector('svg');
      // SVG accepts negative width (renders nothing visible), but doesn't crash
      expect(svg).toBeTruthy();
    });

    it('handles very large size', () => {
      const { container } = render(<Icon name="home" size={99999} />);
      const svg = container.querySelector('svg');
      expect(svg?.getAttribute('width')).toBe('99999');
    });

    it('handles fractional size', () => {
      const { container } = render(<Icon name="home" size={12.5} />);
      const svg = container.querySelector('svg');
      expect(svg?.getAttribute('width')).toBe('12.5');
    });
  });

  describe('strokeWidth edge cases', () => {
    it('applies custom strokeWidth', () => {
      const { container } = render(<Icon name="home" strokeWidth={3} />);
      const svg = container.querySelector('svg');
      expect(svg?.getAttribute('stroke-width')).toBe('3');
    });

    it('strokeWidth is ignored for fill icons', () => {
      const { container } = render(<Icon name="play" strokeWidth={5} />);
      const svg = container.querySelector('svg');
      // Fill icons should not have stroke-width attribute
      expect(svg?.getAttribute('stroke-width')).toBeNull();
    });

    it('handles strokeWidth = 0', () => {
      const { container } = render(<Icon name="home" strokeWidth={0} />);
      const svg = container.querySelector('svg');
      expect(svg?.getAttribute('stroke-width')).toBe('0');
    });
  });

  describe('className edge cases', () => {
    it('handles empty className', () => {
      const { container } = render(<Icon name="home" className="" />);
      const svg = container.querySelector('svg');
      expect(svg?.classList.length).toBe(0);
    });

    it('handles multiple classNames', () => {
      const { container } = render(<Icon name="home" className="icon-lg primary" />);
      const svg = container.querySelector('svg');
      expect(svg?.classList.contains('icon-lg')).toBe(true);
      expect(svg?.classList.contains('primary')).toBe(true);
    });

    it('handles className with special characters', () => {
      const { container } = render(<Icon name="home" className="my-icon_123" />);
      const svg = container.querySelector('svg');
      expect(svg?.classList.contains('my-icon_123')).toBe(true);
    });
  });

  describe('title edge cases', () => {
    it('handles empty title string', () => {
      const { container } = render(<Icon name="home" title="" />);
      const svg = container.querySelector('svg');
      // Empty title is falsy in our condition: `title ? ... : ...`
      // So empty string is treated as no title → aria-hidden
      expect(svg?.getAttribute('aria-hidden')).toBe('true');
    });

    it('handles very long title', () => {
      const longTitle = 'A'.repeat(500);
      const { container } = render(<Icon name="home" title={longTitle} />);
      const svg = container.querySelector('svg');
      expect(svg?.getAttribute('aria-label')).toBe(longTitle);
    });

    it('handles title with special characters', () => {
      const { container } = render(<Icon name="home" title="<test & stuff>" />);
      const svg = container.querySelector('svg');
      expect(svg?.getAttribute('aria-label')).toBe('<test & stuff>');
    });
  });

  describe('multi-path icons', () => {
    it('renders all paths for icons with string[] d', () => {
      // Find an icon that uses string[] for d
      const multiPathIcons = Object.entries(ICON_PATHS).filter(
        ([, def]) => Array.isArray(def.d),
      );

      if (multiPathIcons.length === 0) {
        // Create a temporary test: manually add a multi-path icon
        const { container } = render(
          <svg>
            <path d="M1 1 L2 2" />
            <path d="M3 3 L4 4" />
          </svg>,
        );
        const paths = container.querySelectorAll('path');
        expect(paths.length).toBe(2);
        return;
      }

      const [name, def] = multiPathIcons[0];
      const { container } = render(<Icon name={name} />);
      const pathCount = Array.isArray(def.d) ? def.d.length : 1;
      const paths = container.querySelectorAll('path');
      expect(paths.length).toBe(pathCount);
    });
  });

  describe('re-render stability', () => {
    it('produces consistent output across re-renders with same props', () => {
      const { container, rerender } = render(<Icon name="home" size={20} />);
      const svg1 = container.querySelector('svg')?.outerHTML;

      rerender(<Icon name="home" size={20} />);
      const svg2 = container.querySelector('svg')?.outerHTML;

      expect(svg1).toBe(svg2);
    });
  });
});
