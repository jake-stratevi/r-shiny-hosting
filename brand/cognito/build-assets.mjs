// ---------------------------------------------------------------------------
// Generates every image asset in ./assets from the two staged brand files in
// ../ (stratevi-brandmark.svg, ai-stratevi-lockup.svg). The originals are read
// only; nothing here writes back to them.
//
//   node build-assets.mjs
//
// The ICO favicon is the ONLY thing that needs a rasterizer. If
// @resvg/resvg-js is not resolvable the script still emits every SVG and
// skips the two .ico files with a warning, so the SVG assets can be
// regenerated on a machine with nothing installed.
//
//   npm i @resvg/resvg-js      (in a scratch dir; NODE_PATH=<dir>/node_modules)
//
// Coordinates below are in the LOCKUP's own user space (viewBox 0 0 4381.395
// 1170.466). They were measured off a 4381px render by scanning ink columns:
//
//   287..1917  x  390..780   "ASSEMBLED INTELLIGENCE(tm)"  (two lines)
//   2109..2111 x  266..904   the vertical divider rule
//   2322..2676 x  389..780   the black outline hex-S mark
//   2734..3689 x  466..681   the "stratevi" wordmark
//
// The black hex in the lockup is replaced by the gradient brandmark, whose own
// box (748 x 832) is very nearly the same aspect as the black one (354 x 391).
// ---------------------------------------------------------------------------

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const here = path.dirname(fileURLToPath(import.meta.url));
const brandDir = path.resolve(here, '..');
const outDir = path.join(here, 'assets');
fs.mkdirSync(outDir, { recursive: true });

// --- palette ---------------------------------------------------------------
// Authoritative Stratevi values plus the few neutrals derived from them.
const C = {
  navy: '#0C111B',
  gray: '#283340',
  teal: '#57C4C5',
  blue: '#40C4DF',
  green: '#51BB7F',
  accent: '#74A3D7',
  textOnDark: '#F2F5F8',
  mutedOnDark: '#9BA9BC',
  footerOnDark: '#6E7D8F',
  textOnLight: '#0C111B',
  mutedOnLight: '#55636F',
  footerOnLight: '#7A8798',
};

// The one line of copy the branding system lets us place. Cognito cannot set
// page text, so this lives inside the form-logo image; edit it here and
// re-run.
const TAGLINE = 'Dashboards and models, on demand';

// --- source bodies ---------------------------------------------------------
const read = (f) => fs.readFileSync(path.join(brandDir, f), 'utf8');
const innerOf = (svg) =>
  svg.slice(svg.indexOf('>', svg.indexOf('<svg')) + 1, svg.lastIndexOf('</svg>')).trim();

const markInner = innerOf(read('stratevi-brandmark.svg')); // defs + gradient path
const lockupInner = innerOf(read('ai-stratevi-lockup.svg')); // all-black paths

/** Keep only the lockup elements that start inside [minX, maxX].
 *
 *  Every <path> in the lockup opens with an ABSOLUTE `M` (the rest of the
 *  command stream is relative), and <rect>/<polygon>/<polyline> carry absolute
 *  coordinates, so the first x is a sound proxy for which ink group a glyph
 *  belongs to. This exists only to stop each cropped asset from carrying the
 *  entire 18 KB lockup; the nested <svg> viewport would clip it anyway. */
function lockupSubset(minX, maxX) {
  const kept = [];
  for (const el of lockupInner.match(/<(?:path|rect|polygon|polyline)[^>]*\/?>/g) ?? []) {
    let x = null;
    const d = /\bd="M\s*(-?[\d.]+)/.exec(el);
    const pts = /\bpoints="\s*(-?[\d.]+)/.exec(el);
    const rx = /<rect[^>]*\bx="(-?[\d.]+)"/.exec(el);
    if (d) x = Number(d[1]);
    else if (pts) x = Number(pts[1]);
    else if (rx) x = Number(rx[1]);
    if (x !== null && x >= minX && x <= maxX) kept.push(el);
  }
  return kept.join('\n');
}

// Lockup ink boxes, measured (see header).
const L = {
  mark: { x: 2322, y: 389, w: 354, h: 391 },
  word: { x: 2734, y: 466, w: 955, h: 215 },
  full: { x: 247, y: 226, w: 3482, h: 718 }, // whole lockup + 40u padding
};
const MARK_VB = { w: 748, h: 832 };

const n = (v) => Number(v.toFixed(3));

/** The gradient hexagon, as a nested <svg> so its userSpaceOnUse gradient
 *  keeps resolving against its own 748x832 box. */
const markSvg = (x, y, h, id) => {
  const w = (h * MARK_VB.w) / MARK_VB.h;
  return `<svg x="${n(x)}" y="${n(y)}" width="${n(w)}" height="${n(h)}" viewBox="0 0 ${MARK_VB.w} ${MARK_VB.h}">
${markInner.replace(/brand-gradient/g, id)}
  </svg>`;
};

/** A crop of the corporate lockup, recoloured. A nested <svg> clips to its
 *  own viewport, which is what hides the parts of the lockup outside `box`. */
const lockupCrop = (box, x, y, w, h, fill) =>
  `<svg x="${n(x)}" y="${n(y)}" width="${n(w)}" height="${n(h)}" viewBox="${box.x} ${box.y} ${box.w} ${box.h}">
    <g fill="${fill}">
${lockupSubset(box.x, box.x + box.w)}
    </g>
  </svg>`;

const write = (name, body) => {
  fs.writeFileSync(path.join(outDir, name), body);
  console.log(`  ${name}  ${fs.statSync(path.join(outDir, name)).size} bytes`);
};

// ---------------------------------------------------------------------------
// FORM_LOGO -- 240x60, Cognito's own convention for every logo slot.
// Row 1: gradient mark + "stratevi" wordmark, in the lockup's own proportions.
// Row 2: the tagline.
// Placed OUT / TOP / CENTER, so it sits above the card, not inside it.
// ---------------------------------------------------------------------------
function formLogo({ word, tagline, gradientId }) {
  const W = 240;
  const H = 60;
  const rowH = 36; // height of the mark
  const s = rowH / L.mark.h; // lockup units -> px
  const markW = (rowH * MARK_VB.w) / MARK_VB.h;
  const gap = (L.word.x - (L.mark.x + L.mark.w)) * s;
  const wordW = L.word.w * s;
  const wordH = L.word.h * s;
  const rowW = markW + gap + wordW;
  const x0 = (W - rowW) / 2;
  const rowY = 3;
  // The wordmark's optical baseline sits below the mark's top edge by the same
  // amount it does in the original lockup.
  const wordY = rowY + (L.word.y - L.mark.y) * s;

  return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
  <title>Stratevi</title>
  ${markSvg(x0, rowY, rowH, gradientId)}
  ${lockupCrop(L.word, x0 + markW + gap, wordY, wordW, wordH, word)}
  <text x="${W / 2}" y="53" fill="${tagline}" text-anchor="middle" font-family="-apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif" font-size="10" letter-spacing="0.2">${TAGLINE}</text>
</svg>
`;
}

write('form-logo-dark.svg', formLogo({ word: C.textOnDark, tagline: C.mutedOnDark, gradientId: 'sm-d' }));
write('form-logo-light.svg', formLogo({ word: C.textOnLight, tagline: C.mutedOnLight, gradientId: 'sm-l' }));

// ---------------------------------------------------------------------------
// PAGE_FOOTER_LOGO -- 240x60. The corporate ASSEMBLED INTELLIGENCE | stratevi
// lockup, flat and muted: the endorsement line, not the brand.
// ---------------------------------------------------------------------------
function footerLogo(fill) {
  const W = 240;
  const H = 60;
  const w = 232;
  const h = (w * L.full.h) / L.full.w;
  return `<svg xmlns="http://www.w3.org/2000/svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}">
  <title>Assembled Intelligence | Stratevi</title>
  ${lockupCrop(L.full, (W - w) / 2, (H - h) / 2, w, h, fill)}
</svg>
`;
}

write('footer-logo-dark.svg', footerLogo(C.footerOnDark));
write('footer-logo-light.svg', footerLogo(C.footerOnLight));

// ---------------------------------------------------------------------------
// FAVICON_SVG -- 16x16, Cognito's convention. Just the mark.
// ---------------------------------------------------------------------------
const faviconSvg = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">
  ${markSvg(0.72, 0, 16, 'fv')}
</svg>
`;
write('favicon-dark.svg', faviconSvg);
write('favicon-light.svg', faviconSvg);

// ---------------------------------------------------------------------------
// IDP_BUTTON_ICON -- 16x16, ResourceId "Microsoft365" (the IdP's name in the
// pool). Microsoft's own four-square mark, unaltered colours; it is the one
// image on the page that is deliberately not ours.
// ---------------------------------------------------------------------------
const msIcon = `<svg xmlns="http://www.w3.org/2000/svg" width="16" height="16" viewBox="0 0 16 16">
  <title>Microsoft</title>
  <rect x="0.5" y="0.5" width="7" height="7" fill="#F25022"/>
  <rect x="8.5" y="0.5" width="7" height="7" fill="#7FBA00"/>
  <rect x="0.5" y="8.5" width="7" height="7" fill="#00A4EF"/>
  <rect x="8.5" y="8.5" width="7" height="7" fill="#FFB900"/>
</svg>
`;
write('idp-microsoft365-dark.svg', msIcon);
write('idp-microsoft365-light.svg', msIcon);

// ---------------------------------------------------------------------------
// FAVICON_ICO -- Cognito accepts only .ico here. Packed by hand as a Vista-era
// ICO whose entries are PNGs, at 16 / 32 / 48.
// ---------------------------------------------------------------------------
async function buildIco() {
  let Resvg;
  for (const spec of [process.env.RESVG_MODULE, '@resvg/resvg-js'].filter(Boolean)) {
    try {
      ({ Resvg } = await import(spec));
      break;
    } catch {
      /* try the next candidate */
    }
  }
  if (!Resvg) {
    console.warn('  ! @resvg/resvg-js not resolvable - skipped favicon-*.ico');
    console.warn('    npm i @resvg/resvg-js somewhere, then re-run with');
    console.warn('    RESVG_MODULE=file:///<path>/node_modules/@resvg/resvg-js/index.js');
    return;
  }
  const src = `<svg xmlns="http://www.w3.org/2000/svg" width="64" height="64" viewBox="0 0 64 64">${markSvg(2.9, 0, 64, 'ic')}</svg>`;
  const sizes = [16, 32, 48];
  const pngs = sizes.map((px) =>
    Buffer.from(
      new Resvg(src, { fitTo: { mode: 'height', value: px }, background: 'rgba(0,0,0,0)' })
        .render()
        .asPng(),
    ),
  );

  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0); // reserved
  header.writeUInt16LE(1, 2); // type 1 = icon
  header.writeUInt16LE(sizes.length, 4);
  let offset = 6 + 16 * sizes.length;
  const dir = [];
  sizes.forEach((px, i) => {
    const e = Buffer.alloc(16);
    e.writeUInt8(px === 256 ? 0 : px, 0); // width
    e.writeUInt8(px === 256 ? 0 : px, 1); // height
    e.writeUInt8(0, 2); // palette
    e.writeUInt8(0, 3); // reserved
    e.writeUInt16LE(1, 4); // colour planes
    e.writeUInt16LE(32, 6); // bits per pixel
    e.writeUInt32LE(pngs[i].length, 8);
    e.writeUInt32LE(offset, 12);
    offset += pngs[i].length;
    dir.push(e);
  });
  const ico = Buffer.concat([header, ...dir, ...pngs]);
  for (const name of ['favicon-dark.ico', 'favicon-light.ico']) {
    fs.writeFileSync(path.join(outDir, name), ico);
    console.log(`  ${name}  ${ico.length} bytes`);
  }
}

await buildIco();

// ---------------------------------------------------------------------------
// The manifest apply.ps1 walks. Category / ColorMode / Extension are the
// cognito-idp AssetType enums; ResourceId is only meaningful for
// IDP_BUTTON_ICON, where it names the identity provider.
// ---------------------------------------------------------------------------
const manifest = [
  { file: 'form-logo-dark.svg', category: 'FORM_LOGO', colorMode: 'DARK', extension: 'SVG', element: 'Brand lockup + tagline above the sign-in card' },
  { file: 'form-logo-light.svg', category: 'FORM_LOGO', colorMode: 'LIGHT', extension: 'SVG', element: 'Same, for light mode' },
  { file: 'footer-logo-dark.svg', category: 'PAGE_FOOTER_LOGO', colorMode: 'DARK', extension: 'SVG', element: 'Muted corporate lockup in the page footer' },
  { file: 'footer-logo-light.svg', category: 'PAGE_FOOTER_LOGO', colorMode: 'LIGHT', extension: 'SVG', element: 'Same, for light mode' },
  { file: 'favicon-dark.svg', category: 'FAVICON_SVG', colorMode: 'DARK', extension: 'SVG', element: 'Browser tab icon (modern browsers)' },
  { file: 'favicon-light.svg', category: 'FAVICON_SVG', colorMode: 'LIGHT', extension: 'SVG', element: 'Same, for light mode' },
  { file: 'favicon-dark.ico', category: 'FAVICON_ICO', colorMode: 'DARK', extension: 'ICO', element: 'Browser tab icon (legacy / bookmark)' },
  { file: 'favicon-light.ico', category: 'FAVICON_ICO', colorMode: 'LIGHT', extension: 'ICO', element: 'Same, for light mode' },
  { file: 'idp-microsoft365-dark.svg', category: 'IDP_BUTTON_ICON', colorMode: 'DARK', extension: 'SVG', resourceId: 'Microsoft365', element: 'Icon inside the "Sign in with Microsoft365" button' },
  { file: 'idp-microsoft365-light.svg', category: 'IDP_BUTTON_ICON', colorMode: 'LIGHT', extension: 'SVG', resourceId: 'Microsoft365', element: 'Same, for light mode' },
].filter((a) => fs.existsSync(path.join(outDir, a.file)));

fs.writeFileSync(path.join(outDir, 'manifest.json'), JSON.stringify(manifest, null, 2) + '\n');
console.log(`  manifest.json  ${manifest.length} assets`);
