/// <reference types="node" />
import { readFileSync } from 'node:fs';
import { describe, expect, it } from 'vitest';
import { AMBER, BLUE, GREEN, GREY, RED } from './components/ui';

// WCAG 2.2 contrast for the text and background pairs the theme uses. Body text needs at least 4.5:1.
// Read from disk: vitest doesn't load stylesheet contents.
const css = readFileSync(new URL('./index.css', import.meta.url), 'utf8');

function token(name: string): string {
  const match = css.match(new RegExp(`--${name}:\\s*(#[0-9a-fA-F]{6})`));
  if (!match) throw new Error(`Token --${name} is not a hex colour in index.css`);
  return match[1];
}

function luminance(hex: string): number {
  const channels = [1, 3, 5].map(i => parseInt(hex.slice(i, i + 2), 16) / 255);
  const [r, g, b] = channels.map(c => (c <= 0.03928 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

function contrast(foreground: string, background: string): number {
  const [light, dark] = [luminance(foreground), luminance(background)].sort((a, b) => b - a);
  return (light + 0.05) / (dark + 0.05);
}

const WHITE = '#ffffff';

describe('theme contrast', () => {
  const pairs: [string, string, string][] = [
    ['ink on lab', token('ink'), token('lab')],
    ['grey on white', token('grey'), WHITE],
    ['grey on lab', token('grey'), token('lab')],
    ['navy on white', token('navy'), WHITE],
    ['white on navy', WHITE, token('navy')],
    ['navy on gold', token('navy'), token('gold')],
    ['elec links on white', token('elec'), WHITE],
    ['sidebar links on navy', '#D2DAFA', token('navy')],
    ['green status on white', GREEN, WHITE],
    ['red status on white', RED, WHITE],
    ['amber status on white', AMBER, WHITE],
    ['blue status on white', BLUE, WHITE],
    ['grey status on white', GREY, WHITE],
  ];

  it.each(pairs)('%s is readable', (_label, foreground, background) => {
    expect(contrast(foreground, background)).toBeGreaterThanOrEqual(4.5);
  });
});
