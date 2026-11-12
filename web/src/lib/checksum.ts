import { sha256Hex } from "./sha256";

export interface PointRow { point_id: string; metric: string; step: number; value: number }

const buf = new DataView(new ArrayBuffer(8));

/** 16 hex digits of the IEEE-754 big-endian bits: identical to Python's struct.pack(">d").hex(). */
export function doubleHex(v: number): string {
  buf.setFloat64(0, v, false);
  let s = "";
  for (let i = 0; i < 8; i++) s += buf.getUint8(i).toString(16).padStart(2, "0");
  return s;
}

export function canonicalLine(r: PointRow): string {
  return `${r.point_id}|${r.metric}|${r.step}|${doubleHex(r.value)}\n`;
}

/** point_id is ASCII-only (server rule), so default string order == UTF-8 byte order. */
export function checksum(rows: PointRow[]): string {
  const sorted = [...rows].sort((a, b) => (a.point_id < b.point_id ? -1 : a.point_id > b.point_id ? 1 : 0));
  return sha256Hex(new TextEncoder().encode(sorted.map(canonicalLine).join("")));
}
