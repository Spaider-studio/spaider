/**
 * Generates assets/icon.png (256x256) and assets/icon.ico (16/32/48 BMP +
 * 256 PNG) of the SpAIder spider-web logo with a pure-JS PNG/ICO writer.
 * No native dependencies: only node:zlib for the PNG deflate stream.
 *
 * The geometry mirrors the SVG in frontend/src/components/layout/Sidebar.tsx
 * (viewBox 0 0 24 24, stroke 1.5, circles r=10/6/2, four spokes).
 */
import fs from "node:fs";
import path from "node:path";
import zlib from "node:zlib";

const outDir = path.resolve(import.meta.dirname, "..", "assets");
fs.mkdirSync(outDir, { recursive: true });

const BG = [0x0a, 0x0a, 0x0f];
const BLUE = [0x3b, 0x82, 0xf6];

/** Renders the logo at `size` px, returns RGBA buffer (premultiplied = false). */
function render(size, supersample = 4) {
  const rgba = Buffer.alloc(size * size * 4);
  const k = size / 24; // SVG units -> px
  const cx = 12 * k;
  const cy = 12 * k;
  const stroke = 1.5 * k;
  const radius = size * 0.22; // rounded-square corner radius

  const circles = [
    { r: 10 * k, a: 0.2 },
    { r: 6 * k, a: 0.35 },
  ];
  const lines = [
    [12, 2, 12, 22],
    [2, 12, 22, 12],
    [4.9, 4.9, 19.1, 19.1],
    [19.1, 4.9, 4.9, 19.1],
  ].map(([x1, y1, x2, y2]) => ({ x1: x1 * k, y1: y1 * k, x2: x2 * k, y2: y2 * k, a: 0.5 }));
  const dotR = 2 * k;

  const insideRoundedSquare = (x, y) => {
    const dx = Math.max(radius - x, 0, x - (size - radius));
    const dy = Math.max(radius - y, 0, y - (size - radius));
    return dx * dx + dy * dy <= radius * radius;
  };
  const segDist = (px, py, l) => {
    const vx = l.x2 - l.x1;
    const vy = l.y2 - l.y1;
    const t = Math.max(0, Math.min(1, ((px - l.x1) * vx + (py - l.y1) * vy) / (vx * vx + vy * vy)));
    const qx = l.x1 + t * vx;
    const qy = l.y1 + t * vy;
    return Math.hypot(px - qx, py - qy);
  };

  const n = supersample;
  for (let y = 0; y < size; y++) {
    for (let x = 0; x < size; x++) {
      let r = 0;
      let g = 0;
      let b = 0;
      let a = 0;
      for (let sy = 0; sy < n; sy++) {
        for (let sx = 0; sx < n; sx++) {
          const px = x + (sx + 0.5) / n;
          const py = y + (sy + 0.5) / n;
          if (!insideRoundedSquare(px, py)) continue;
          let cr = BG[0];
          let cg = BG[1];
          let cb = BG[2];
          const paint = (alpha) => {
            cr = cr + (BLUE[0] - cr) * alpha;
            cg = cg + (BLUE[1] - cg) * alpha;
            cb = cb + (BLUE[2] - cb) * alpha;
          };
          const d = Math.hypot(px - cx, py - cy);
          for (const c of circles) if (Math.abs(d - c.r) <= stroke / 2) paint(c.a);
          for (const l of lines) if (segDist(px, py, l) <= stroke / 2) paint(l.a);
          if (d <= dotR) paint(1);
          r += cr;
          g += cg;
          b += cb;
          a += 1;
        }
      }
      const i = (y * size + x) * 4;
      if (a > 0) {
        rgba[i] = Math.round(r / a);
        rgba[i + 1] = Math.round(g / a);
        rgba[i + 2] = Math.round(b / a);
        rgba[i + 3] = Math.round((a / (n * n)) * 255);
      }
    }
  }
  return rgba;
}

// ---------------------------------------------------------------------------
// PNG
// ---------------------------------------------------------------------------
const CRC_TABLE = new Uint32Array(256).map((_, c) => {
  for (let k = 0; k < 8; k++) c = c & 1 ? 0xedb88320 ^ (c >>> 1) : c >>> 1;
  return c >>> 0;
});
function crc32(buf) {
  let c = 0xffffffff;
  for (const byte of buf) c = CRC_TABLE[(c ^ byte) & 0xff] ^ (c >>> 8);
  return (c ^ 0xffffffff) >>> 0;
}
function chunk(type, data) {
  const len = Buffer.alloc(4);
  len.writeUInt32BE(data.length);
  const typeBuf = Buffer.from(type, "ascii");
  const crc = Buffer.alloc(4);
  crc.writeUInt32BE(crc32(Buffer.concat([typeBuf, data])));
  return Buffer.concat([len, typeBuf, data, crc]);
}
function encodePng(size, rgba) {
  const ihdr = Buffer.alloc(13);
  ihdr.writeUInt32BE(size, 0);
  ihdr.writeUInt32BE(size, 4);
  ihdr[8] = 8; // bit depth
  ihdr[9] = 6; // RGBA
  const raw = Buffer.alloc((size * 4 + 1) * size);
  for (let y = 0; y < size; y++) {
    raw[y * (size * 4 + 1)] = 0; // filter: none
    rgba.copy(raw, y * (size * 4 + 1) + 1, y * size * 4, (y + 1) * size * 4);
  }
  return Buffer.concat([
    Buffer.from([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a]),
    chunk("IHDR", ihdr),
    chunk("IDAT", zlib.deflateSync(raw, { level: 9 })),
    chunk("IEND", Buffer.alloc(0)),
  ]);
}

// ---------------------------------------------------------------------------
// ICO (BMP/DIB entries for small sizes, PNG entry for 256)
// ---------------------------------------------------------------------------
function encodeDib(size, rgba) {
  const header = Buffer.alloc(40);
  header.writeUInt32LE(40, 0);
  header.writeInt32LE(size, 4);
  header.writeInt32LE(size * 2, 8); // XOR + AND mask height
  header.writeUInt16LE(1, 12);
  header.writeUInt16LE(32, 14);
  const xorSize = size * size * 4;
  const maskRow = Math.ceil(size / 32) * 4;
  const andSize = maskRow * size;
  header.writeUInt32LE(xorSize + andSize, 20);
  const xor = Buffer.alloc(xorSize);
  const and = Buffer.alloc(andSize);
  for (let y = 0; y < size; y++) {
    const srcRow = size - 1 - y; // bottom-up
    for (let x = 0; x < size; x++) {
      const s = (srcRow * size + x) * 4;
      const d = (y * size + x) * 4;
      xor[d] = rgba[s + 2];
      xor[d + 1] = rgba[s + 1];
      xor[d + 2] = rgba[s];
      xor[d + 3] = rgba[s + 3];
      if (rgba[s + 3] === 0) and[y * maskRow + (x >> 3)] |= 0x80 >> (x & 7);
    }
  }
  return Buffer.concat([header, xor, and]);
}
function encodeIco(entries) {
  const header = Buffer.alloc(6);
  header.writeUInt16LE(0, 0);
  header.writeUInt16LE(1, 2);
  header.writeUInt16LE(entries.length, 4);
  const dir = Buffer.alloc(16 * entries.length);
  let offset = 6 + dir.length;
  const blobs = [];
  entries.forEach((e, i) => {
    dir[i * 16] = e.size >= 256 ? 0 : e.size;
    dir[i * 16 + 1] = e.size >= 256 ? 0 : e.size;
    dir[i * 16 + 2] = 0;
    dir[i * 16 + 3] = 0;
    dir.writeUInt16LE(1, i * 16 + 4);
    dir.writeUInt16LE(32, i * 16 + 6);
    dir.writeUInt32LE(e.data.length, i * 16 + 8);
    dir.writeUInt32LE(offset, i * 16 + 12);
    offset += e.data.length;
    blobs.push(e.data);
  });
  return Buffer.concat([header, dir, ...blobs]);
}

const png256 = encodePng(256, render(256, 4));
fs.writeFileSync(path.join(outDir, "icon.png"), png256);

const ico = encodeIco([
  { size: 16, data: encodeDib(16, render(16, 8)) },
  { size: 32, data: encodeDib(32, render(32, 6)) },
  { size: 48, data: encodeDib(48, render(48, 5)) },
  { size: 256, data: png256 },
]);
fs.writeFileSync(path.join(outDir, "icon.ico"), ico);

console.log(`wrote ${path.join(outDir, "icon.png")} (${png256.length} bytes)`);
console.log(`wrote ${path.join(outDir, "icon.ico")} (${ico.length} bytes)`);
