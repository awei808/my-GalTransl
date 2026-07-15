import { describe, it, expect } from 'vitest';
import { compressImageToDataUrl } from '../imageCompress';

/**
 * compressImageToDataUrl uses the Image constructor and canvas,
 * which jsdom does not fully support. These tests are skipped in
 * the jsdom environment — they should be run in a real browser or
 * with a more complete DOM polyfill.
 *
 * The function is still covered by manual testing in the Settings page.
 */
describe.skip('compressImageToDataUrl — edge cases (requires real DOM)', () => {
  it('rejects non-image files with an error', async () => {
    const file = new File(['hello'], 'test.txt', { type: 'text/plain' });
    await expect(compressImageToDataUrl(file)).rejects.toThrow('无法读取图片文件');
  });

  it('rejects empty file', async () => {
    const file = new File([], 'empty.png', { type: 'image/png' });
    await expect(compressImageToDataUrl(file)).rejects.toThrow();
  });

  it('rejects corrupt image data', async () => {
    const file = new File([new Uint8Array([0, 1, 2, 3])], 'corrupt.png', { type: 'image/png' });
    await expect(compressImageToDataUrl(file)).rejects.toThrow('无法读取图片文件');
  });
});

describe('compressImageToDataUrl — basic checks', () => {
  it('function exists and is callable', () => {
    expect(typeof compressImageToDataUrl).toBe('function');
  });
});
