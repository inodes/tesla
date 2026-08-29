/**
 * Tesla Dashcam MP4 Parser
 * Parses MP4 files and extracts SEI metadata from Tesla dashcam footage.
 *
 * Source: https://github.com/teslamotors/dashcam
 */

export interface SeiMetadataType {
  decode(data: Uint8Array): SeiData;
}

export interface SeiData {
  version?: number;
  gear_state?: number;
  frame_seq_no?: number | bigint;
  vehicle_speed_mps?: number;
  accelerator_pedal_position?: number;
  steering_wheel_angle?: number;
  blinker_on_left?: boolean;
  blinker_on_right?: boolean;
  brake_applied?: boolean;
  autopilot_state?: number;
  latitude_deg?: number;
  longitude_deg?: number;
  heading_deg?: number;
  linear_acceleration_mps2_x?: number;
  linear_acceleration_mps2_y?: number;
  linear_acceleration_mps2_z?: number;
}

interface BoxResult {
  start: number;
  end: number;
  size: number;
}

interface MdatResult {
  offset: number;
  size: number;
}

interface VideoConfig {
  width: number;
  height: number;
  codec: string;
  sps: Uint8Array;
  pps: Uint8Array;
  timescale: number;
  durations: number[];
}

export interface SeiWithFrameIndex {
  frameIndex: number;
  sei: SeiData;
}

export class DashcamMP4 {
  private buffer: ArrayBuffer;
  private view: DataView;
  private _config: VideoConfig | null = null;

  constructor(buffer: ArrayBuffer) {
    this.buffer = buffer;
    this.view = new DataView(buffer);
  }

  // -------------------------------------------------------------
  // MP4 Box Navigation
  // -------------------------------------------------------------

  /** Find a box by name within a range */
  findBox(start: number, end: number, name: string): BoxResult {
    for (let pos = start; pos + 8 <= end; ) {
      let size = this.view.getUint32(pos);
      const type = this.readAscii(pos + 4, 4);
      const headerSize = size === 1 ? 16 : 8;

      if (size === 1) {
        const high = this.view.getUint32(pos + 8);
        const low = this.view.getUint32(pos + 12);
        size = Number((BigInt(high) << 32n) | BigInt(low));
      } else if (size === 0) {
        size = end - pos;
      }

      if (type === name) {
        return { start: pos + headerSize, end: pos + size, size: size - headerSize };
      }
      pos += size;
    }
    throw new Error(`Box "${name}" not found`);
  }

  /** Find mdat box and return content location */
  findMdat(): MdatResult {
    const mdat = this.findBox(0, this.view.byteLength, 'mdat');
    return { offset: mdat.start, size: mdat.size };
  }

  // -------------------------------------------------------------
  // Video Configuration
  // -------------------------------------------------------------

  /** Get video configuration (lazy-loaded) */
  getConfig(): VideoConfig {
    if (this._config) return this._config;

    const moov = this.findBox(0, this.view.byteLength, 'moov');
    const trak = this.findBox(moov.start, moov.end, 'trak');
    const mdia = this.findBox(trak.start, trak.end, 'mdia');
    const minf = this.findBox(mdia.start, mdia.end, 'minf');
    const stbl = this.findBox(minf.start, minf.end, 'stbl');
    const stsd = this.findBox(stbl.start, stbl.end, 'stsd');
    const avc1 = this.findBox(stsd.start + 8, stsd.end, 'avc1');
    const avcC = this.findBox(avc1.start + 78, avc1.end, 'avcC');

    const o = avcC.start;
    const codec = `avc1.${this.hex(this.view.getUint8(o + 1))}${this.hex(this.view.getUint8(o + 2))}${this.hex(this.view.getUint8(o + 3))}`;

    // Extract SPS/PPS
    let p = o + 6;
    const spsLen = this.view.getUint16(p);
    const sps = new Uint8Array(this.buffer.slice(p + 2, p + 2 + spsLen));
    p += 2 + spsLen + 1;
    const ppsLen = this.view.getUint16(p);
    const pps = new Uint8Array(this.buffer.slice(p + 2, p + 2 + ppsLen));

    // Get timescale from mdhd
    const mdhd = this.findBox(mdia.start, mdia.end, 'mdhd');
    const mdhdVersion = this.view.getUint8(mdhd.start);
    const timescale = mdhdVersion === 1
      ? this.view.getUint32(mdhd.start + 20)
      : this.view.getUint32(mdhd.start + 12);

    // Get frame durations from stts
    const stts = this.findBox(stbl.start, stbl.end, 'stts');
    const entryCount = this.view.getUint32(stts.start + 4);
    const durations: number[] = [];
    let pos = stts.start + 8;
    for (let i = 0; i < entryCount; i++) {
      const count = this.view.getUint32(pos);
      const delta = this.view.getUint32(pos + 4);
      const ms = (delta / timescale) * 1000;
      for (let j = 0; j < count; j++) durations.push(ms);
      pos += 8;
    }

    this._config = {
      width: this.view.getUint16(avc1.start + 24),
      height: this.view.getUint16(avc1.start + 26),
      codec,
      sps,
      pps,
      timescale,
      durations,
    };
    return this._config;
  }

  // -------------------------------------------------------------
  // SEI Extraction
  // -------------------------------------------------------------

  /** Extract all SEI messages with frame index for timeline mapping */
  extractSeiMessagesWithFrameIndex(SeiMetadata: SeiMetadataType): SeiWithFrameIndex[] {
    const mdat = this.findMdat();
    const messages: SeiWithFrameIndex[] = [];
    let cursor = mdat.offset;
    const end = mdat.offset + mdat.size;
    let frameIndex = 0;

    // Debug counters
    let nalCount = 0;
    let seiCount = 0;
    let seiType5Count = 0;
    let decodedCount = 0;
    const nalTypes: Record<number, number> = {};

    while (cursor + 4 <= end) {
      const nalSize = this.view.getUint32(cursor);
      cursor += 4;

      if (nalSize < 2 || cursor + nalSize > this.view.byteLength) {
        cursor += Math.max(nalSize, 0);
        continue;
      }

      nalCount++;
      const nalType = this.view.getUint8(cursor) & 0x1f;
      nalTypes[nalType] = (nalTypes[nalType] || 0) + 1;

      // NAL type 6 = SEI
      if (nalType === 6) {
        seiCount++;
        const payloadType = this.view.getUint8(cursor + 1);

        // payload type 5 = user data unregistered (Tesla's SEI)
        if (payloadType === 5) {
          seiType5Count++;
          const sei = this.decodeSei(
            new Uint8Array(this.buffer.slice(cursor, cursor + nalSize)),
            SeiMetadata
          );
          if (sei) {
            decodedCount++;
            messages.push({ frameIndex, sei });
          }
        }
      }

      // Count frames (type 5 = IDR, type 1 = non-IDR)
      if (nalType === 5 || nalType === 1) {
        frameIndex++;
      }

      cursor += nalSize;
    }

    console.log('[MP4] NAL unit stats:', {
      totalNALs: nalCount,
      nalTypes: nalTypes,
      seiNALs: seiCount,
      seiType5: seiType5Count,
      successfullyDecoded: decodedCount,
      totalFrames: frameIndex
    });

    return messages;
  }

  /** Decode SEI NAL unit to protobuf message */
  decodeSei(nal: Uint8Array, SeiMetadata: SeiMetadataType): SeiData | null {
    if (!SeiMetadata || nal.length < 4) return null;

    let i = 3;
    while (i < nal.length && nal[i] === 0x42) i++;
    if (i <= 3 || i + 1 >= nal.length || nal[i] !== 0x69) return null;

    try {
      return SeiMetadata.decode(this.stripEmulationBytes(nal.subarray(i + 1, nal.length - 1)));
    } catch {
      return null;
    }
  }

  /** Strip H.264 emulation prevention bytes */
  stripEmulationBytes(data: Uint8Array): Uint8Array {
    const out: number[] = [];
    let zeros = 0;
    for (const byte of data) {
      if (zeros >= 2 && byte === 0x03) {
        zeros = 0;
        continue;
      }
      out.push(byte);
      zeros = byte === 0 ? zeros + 1 : 0;
    }
    return Uint8Array.from(out);
  }

  // -------------------------------------------------------------
  // Utilities
  // -------------------------------------------------------------

  private readAscii(start: number, len: number): string {
    let s = '';
    for (let i = 0; i < len; i++) s += String.fromCharCode(this.view.getUint8(start + i));
    return s;
  }

  private hex(n: number): string {
    return n.toString(16).padStart(2, '0');
  }

  /** Get FPS from video configuration */
  getFps(): number {
    const config = this.getConfig();
    if (config.durations.length > 0) {
      const avgDuration = config.durations.reduce((a, b) => a + b, 0) / config.durations.length;
      return 1000 / avgDuration;
    }
    return 30; // Default fallback
  }
}
