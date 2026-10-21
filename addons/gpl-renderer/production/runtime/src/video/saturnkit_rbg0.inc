// Existing MIT SaturnKit RBG0 port, source e4a6fbdb8bb838e72c16b88b0a68feb6f3dad908.
// Copyright (c) 2026 Samuele Voltan. See SATURNKIT-RBG0-LICENSE.txt.
// Sampling/special helpers are prefixed to preserve the native NBG path.
// the dot of a character (or bitmap) at (x, y) in its own pixels: false if transparent
static bool rbg_dot(const Nbg& s, uint32_t base, int x, int y, int width, uint32_t pal, Pix& p) {
    uint32_t i = (uint32_t)(y * width + x);
    uint32_t v;
    switch (s.colors) {
    case 0: v = vb(base + i / 2); v = (i & 1) ? (v & 0xF) : (v >> 4); break;
    case 1: v = vb(base + i); break;
    case 2: v = vw(base + i * 2) & 0x7FF; break;
    case 3: {
        uint16_t w = vw(base + i * 2);
        if (!(w & 0x8000) && !s.opaque) return false;
        p.rgb = rgb555(w);
        p.direct = true; p.msb = w >> 15;
        return true;
    }
    default: {
        uint32_t w = (uint32_t)vw(base + i * 4) << 16 | vw(base + i * 4 + 2);
        if (!(w & 0x80000000u) && !s.opaque) return false;
        p.rgb = (w & 0xFF) << 16 | (w & 0xFF00) | (w >> 16 & 0xFF);
        p.direct = true; p.msb = w >> 31;
        return true;
    }
    }
    if (v == 0 && !s.opaque) return false;
    uint32_t index = s.colors == 0 ? pal * 16 + v : s.colors == 1 ? (pal & 0x70) * 16 + v : v;
    p.rgb = cram(index + s.caos, &p.msb);
    p.direct = false; p.dcc = (uint16_t)v;
    return true;
}

// The cell of a map of planes (side x side of them, at the addresses in planes[]) at map
// coordinates (sx, sy), already wrapped into the map; or, with pnd_over set, the cell named
// by the pattern name *pnd_over (a rotation screen's screen-over pattern).
static bool rbg_cell_pixel(const Nbg& s, const uint32_t* planes, int side, uint32_t sx, uint32_t sy, Pix& p,
                       const uint16_t* pnd_over = nullptr) {
    uint32_t pw = (uint32_t)s.plane_w * 512, ph = (uint32_t)s.plane_h * 512;
    int cs = s.cell2x2 ? 16 : 8;
    uint32_t ix = sx % pw, iy = sy % ph;
    uint32_t pnd_addr = 0;
    if (!pnd_over) {
        int pl = (int)(sy / ph) * side + (int)(sx / pw);
        uint32_t page = (iy / 512) * (uint32_t)s.plane_w + ix / 512;
        uint32_t cx = (ix % 512) / (uint32_t)cs, cy = (iy % 512) / (uint32_t)cs;
        uint32_t cpr = 512 / (uint32_t)cs;
        pnd_addr = planes[pl] + page * s.page_bytes + (cy * cpr + cx) * (s.word1 ? 2 : 4);
    }
    uint32_t chr, pal;
    bool hf, vf;
    if (s.word1 || pnd_over) {
        uint16_t w = pnd_over ? *pnd_over : vw(pnd_addr);
        p.spr = s.supp >> 9 & 1; p.scc = s.supp >> 8 & 1;
        pal = s.colors == 0 ? (uint32_t)(w >> 12 & 0xF) | (s.supp >> 1 & 0x70) : (uint32_t)(w >> 8 & 0x70);
        if (!s.cnsm) {
            hf = w & 0x400; vf = w & 0x800;
            chr = s.cell2x2 ? ((w & 0x3FFu) << 2) | (s.supp & 3u) | ((s.supp & 0x1Cu) << 10)
                            : (w & 0x3FFu) | ((s.supp & 0x1Fu) << 10);
        } else {
            hf = vf = false;
            chr = s.cell2x2 ? ((w & 0xFFFu) << 2) | (s.supp & 3u) | ((s.supp & 0x10u) << 10)
                            : (w & 0xFFFu) | ((s.supp & 0x1Cu) << 10);
        }
    } else {
        uint16_t w0 = vw(pnd_addr), w1 = vw(pnd_addr + 2);
        hf = w0 & 0x4000; vf = w0 & 0x8000;
        p.spr = w0 >> 13 & 1; p.scc = w0 >> 12 & 1;
        pal = w0 & 0x7F;
        chr = w1 & 0x7FFF;
    }
    int px = (int)(ix % (uint32_t)cs), py = (int)(iy % (uint32_t)cs);
    if (hf) px = cs - 1 - px;
    if (vf) py = cs - 1 - py;
    static const uint32_t kCellBytes[] = {32, 64, 128, 128, 256};
    uint32_t base = chr * 0x20;
    if (s.cell2x2) base += (uint32_t)((py >> 3) * 2 + (px >> 3)) * kCellBytes[s.colors];
    return rbg_dot(s, base, px & 7, py & 7, 8, pal, p);
}

// ---- the rotation screen RBG0 -----------------------------------------------------------------
// Each dot's map coordinates come from rotation parameter A or B (RPMD: A, B, or B where A's
// coefficient is transparent). A parameter is a table in VRAM (RPTA, A then B 0x80 bytes on):
// the screen's start (Xst, Yst, Zst) and its steps per line (dXst, dYst) and per dot (dX, dY),
// the matrix A-F, the viewpoint P, the centre C, the shift M and the scale kx, ky. A coefficient
// table (KTCTL, KTAOF) may give, per line or per dot, kx and ky, one of them, or Xp, and a
// transparent bit. The map is 4x4 planes of the parameter's plane size, or a bitmap; outside it
// (PLSZ's screen-over) it repeats, shows one pattern (OVPNR), or is transparent.
struct RotParam {
    Nbg chr;                                    // characters, plane size, the map's pattern names
    uint32_t plane[16];
    int32_t xst, yst, zst, dxst, dyst, dx, dy, m[6], px, py, pz, cx, cy, cz, mx, my, kx, ky;
    uint32_t ka0;                               // KTAOF and KAst, 10 fraction bits
    int32_t dkast, dkax;
    bool coef, coef1w, coefficient_line_color;
    int coef_mode, over;
    uint16_t over_pn;
    // on the current line
    int32_t xsp, ysp, xp, yp, ldx, ldy;
    uint32_t ka, line_coef;
};

struct Rbg {
    bool on;
    int mode;                                   // RPMD
    bool crkte, perdot, bank_coef[4];
    RotParam par[2];
    uint8_t prio, ratio;
    bool cc, offset, offset_b;
};

static int32_t sext(uint32_t v, int bits) { return (int32_t)(v << (32 - bits)) >> (32 - bits); }
static uint32_t vl(uint32_t a) { return (uint32_t)vw(a) << 16 | vw(a + 2); }

static void setup_rbg(Rbg& r, bool sprite_rotation = false) {
    r = Rbg{};
    uint16_t bgon = reg(0x20);
    r.on = bgon >> 4 & 1;
    if (!r.on && !sprite_rotation) return;
    r.mode = reg(0xB0) & 3;
    uint16_t ramctl = reg(0x0E);
    r.crkte = ramctl >> 15 & 1;
    for (int b = 0; b < 4; ++b) {                // which VRAM banks hold coefficients (RDBS), split or not
        int esb = b & (2 | (ramctl >> (8 + (b >> 1)) & 1));
        r.bank_coef[b] = r.crkte || (ramctl >> (esb * 2) & 3) == 1;
        r.perdot |= r.bank_coef[b];
    }
    r.prio = (uint8_t)(reg(0xFC) & 7);
    r.ratio = (uint8_t)(reg(0x10C) & 0x1F);
    r.cc = reg(0xEC) >> 4 & 1;
    r.offset = reg(0x110) >> 4 & 1;
    r.offset_b = reg(0x112) >> 4 & 1;
    uint16_t chctl = (uint16_t)(reg(0x2A) >> 8), pncr = reg(0x38);
    uint32_t rpta = ((uint32_t)(reg(0xBC) & 7) << 16 | (reg(0xBE) & 0xFFFE)) * 2 & 0xFFF7C;
    for (int i = 0; i < 2; ++i) {
        RotParam& p = r.par[i];
        Nbg& s = p.chr;
        s.on = true;
        s.opaque = bgon >> 12 & 1;
        s.colors = chctl >> 4 & 7;
        s.bitmap = chctl >> 1 & 1;               // both parameters describe the same RBG0 format
        s.cell2x2 = chctl & 1;
        s.bm_w = 512;
        s.bm_h = chctl >> 2 & 1 ? 512 : 256;
        s.bm_pal = (uint16_t)((reg(0x2E) & 7) << 4);
        s.bm_spr = reg(0x2E) >> 5 & 1;
        s.bm_scc = reg(0x2E) >> 4 & 1;
        s.word1 = pncr >> 15 & 1;
        s.cnsm = pncr >> 14 & 1;
        s.supp = pncr & 0x3FF;
        s.caos = (uint32_t)(reg(0xE6) & 7) << 8;
        int plsz = reg(0x3A) >> (8 + i * 4) & 3;
        s.plane_w = plsz == 0 ? 1 : 2;
        s.plane_h = plsz == 3 ? 2 : 1;
        s.page_bytes = (s.cell2x2 ? 0x800u : 0x2000u) * (s.word1 ? 1 : 2);
        p.over = reg(0x3A) >> (10 + i * 4) & 3;
        p.over_pn = reg(0xB8 + i * 2);
        uint32_t mpof = reg(0x3E) >> (i * 4) & 7, mask = (uint32_t)(s.plane_w * s.plane_h - 1);
        for (int k = 0; k < 16; ++k) {
            uint32_t mp = reg(0x50 + i * 0x10 + (k / 2) * 2) >> ((k & 1) * 8) & 0x3F;
            p.plane[k] = ((mpof << 6 | mp) & ~mask) * s.page_bytes;
        }
        s.bm_addr = mpof * 0x20000;
        uint32_t a = rpta + (uint32_t)i * 0x80;
        p.xst = sext(vl(a + 0x00) >> 6, 23);
        p.yst = sext(vl(a + 0x04) >> 6, 23);
        p.zst = sext(vl(a + 0x08) >> 6, 23);
        p.dxst = sext(vl(a + 0x0C) >> 6, 13);
        p.dyst = sext(vl(a + 0x10) >> 6, 13);
        p.dx = sext(vl(a + 0x14) >> 6, 13);
        p.dy = sext(vl(a + 0x18) >> 6, 13);
        for (int k = 0; k < 6; ++k) p.m[k] = sext(vl(a + 0x1C + k * 4) >> 6, 14);
        p.px = sext(vw(a + 0x34), 14); p.py = sext(vw(a + 0x36), 14); p.pz = sext(vw(a + 0x38), 14);
        p.cx = sext(vw(a + 0x3C), 14); p.cy = sext(vw(a + 0x3E), 14); p.cz = sext(vw(a + 0x40), 14);
        p.mx = sext(vl(a + 0x44) >> 6, 24);
        p.my = sext(vl(a + 0x48) >> 6, 24);
        p.kx = sext(vl(a + 0x4C), 24);
        p.ky = sext(vl(a + 0x50), 24);
        uint16_t ktctl = (uint16_t)(reg(0xB4) >> (i * 8));
        p.coef = ktctl & 1;
        p.coef1w = ktctl >> 1 & 1;
        p.coefficient_line_color = p.coef && !p.coef1w && (ktctl & 0x10);
        p.coef_mode = ktctl >> 2 & 3;
        p.ka0 = ((uint32_t)(reg(0xB6) >> (i * 8) & 7) << 26) + (vl(a + 0x54) >> 6);
        p.dkast = sext(vl(a + 0x58) >> 6, 20);
        p.dkax = sext(vl(a + 0x5C) >> 6, 20);
    }
}

// a coefficient's address (in words) from its table address (10 fraction bits)
static uint32_t coef_addr(const Rbg& r, const RotParam& p, uint32_t ka) {
    uint32_t a = (ka >> 10) << (p.coef1w ? 0 : 1);
    return a & (r.crkte ? 0x3FFu : 0x3FFFFu);
}

// a coefficient: bit 31 transparent, bits 23-0 the value (7.16)
static uint32_t coef_read(const Rbg& r, const RotParam& p, uint32_t addr) {
    auto word = [&](uint32_t w) -> uint16_t {
        if (!r.crkte) return vw(w * 2);
        uint32_t b = (0x800 + w * 2) & 0xFFE;
        return (uint16_t)(g_vdp2_cram[b] << 8 | g_vdp2_cram[b + 1]);
    };
    if (p.coef1w) {
        uint16_t t = word(addr);
        return ((uint32_t)sext((uint32_t)t << 6, 21) & 0xFFFFFF) | (uint32_t)(t & 0x8000) << 16;
    }
    return (uint32_t)word(addr) << 16 | word(addr + 1);
}

static void rbg_line(Rbg& r, int y) {
    for (RotParam& p : r.par) {
        int64_t x0 = p.xst + (int64_t)p.dxst * y - p.px * 1024, y0 = p.yst + (int64_t)p.dyst * y - p.py * 1024;
        int64_t z0 = p.zst - p.pz * 1024;
        p.xsp = (int32_t)((p.m[0] * x0 + p.m[1] * y0 + p.m[2] * z0) >> 10);
        p.ysp = (int32_t)((p.m[3] * x0 + p.m[4] * y0 + p.m[5] * z0) >> 10);
        p.xp = p.m[0] * (p.px - p.cx) + p.m[1] * (p.py - p.cy) + p.m[2] * (p.pz - p.cz) + p.cx * 1024 + p.mx;
        p.yp = p.m[3] * (p.px - p.cx) + p.m[4] * (p.py - p.cy) + p.m[5] * (p.pz - p.cz) + p.cy * 1024 + p.my;
        p.ldx = (p.m[0] * p.dx + p.m[1] * p.dy) >> 10;
        p.ldy = (p.m[3] * p.dx + p.m[4] * p.dy) >> 10;
        p.ka = p.ka0 + (uint32_t)(p.dkast * y);
        p.line_coef = coef_read(r, p, coef_addr(r, p, p.ka));
    }
}

// Sega §6.4: mode2 takes the line colour tag from A even when graphics use B.
// Only exports colour metadata; rotation coordinates and sampling are unchanged.
static bool rbg_line_color_tag(const Rbg& r, int x, bool parameter_window, uint8_t& tag) {
    int select = r.mode == 1 || (r.mode == 3 && parameter_window) ? 1 : 0;
    const RotParam& p = r.par[select];
    if (!p.coefficient_line_color) return false;
    uint32_t coefficient = p.line_coef;
    if (r.perdot) {
        uint32_t a = coef_addr(r, p, p.ka + (uint32_t)(x * p.dkax));
        coefficient = r.bank_coef[r.crkte ? 0 : (a >> 16) & 3] ? coef_read(r, p, a) : 0u;
    }
    tag = (uint8_t)(coefficient >> 24 & 0x7F);
    return true;
}

static bool rbg_pixel(const Rbg& r, int x, bool parameter_window, Pix& out) {
    int sel = r.mode == 1 || (r.mode == 3 && parameter_window) ? 1 : 0;
    auto coef_at = [&](int i) {
        const RotParam& p = r.par[i];
        if (!r.perdot) return p.line_coef;
        uint32_t a = coef_addr(r, p, p.ka + (uint32_t)(x * p.dkax));
        return r.bank_coef[r.crkte ? 0 : (a >> 16) & 3] ? coef_read(r, p, a) : 0u;
    };
    uint32_t c = 0;
    if (r.mode == 2) {
        c = r.par[0].coef ? coef_at(0) : 0;
        if (c >> 31) { sel = 1; c = r.par[1].line_coef; }
    } else if (r.par[sel].coef) {
        c = coef_at(sel);
    }
    const RotParam& p = r.par[sel];
    int32_t kx = p.kx, ky = p.ky;
    uint32_t xp = (uint32_t)p.xp;
    if (p.coef) {
        if (c >> 31) return false;              // a transparent coefficient
        int32_t v = sext(c, 24);
        switch (p.coef_mode) {
        case 0: kx = ky = v; break;
        case 1: kx = v; break;
        case 2: ky = v; break;
        default: xp = (uint32_t)v << 2; break;
        }
    }
    uint32_t ix = (xp + (uint32_t)(((int64_t)kx * (int32_t)(p.xsp + p.ldx * x)) >> 16)) >> 10;
    uint32_t iy = ((uint32_t)p.yp + (uint32_t)(((int64_t)ky * (int32_t)(p.ysp + p.ldy * x)) >> 16)) >> 10;
    const Nbg& s = p.chr;
    uint32_t mw = s.bitmap ? (uint32_t)s.bm_w : (uint32_t)s.plane_w * 512 * 4;
    uint32_t mh = s.bitmap ? (uint32_t)s.bm_h : (uint32_t)s.plane_h * 512 * 4;
    bool outside = false;
    if (p.over == 3) outside = (ix | iy) & ~511u;
    else if (p.over) outside = (ix & ~(mw - 1)) || (iy & ~(mh - 1));
    if (outside && (p.over & 2)) return false;
    if (s.bitmap) {
        out.spr = s.bm_spr; out.scc = s.bm_scc;
        return rbg_dot(s, s.bm_addr, (int)(ix & (mw - 1)), (int)(iy & (mh - 1)), s.bm_w, s.bm_pal, out);
    }
    return rbg_cell_pixel(s, p.plane, 4, ix & (mw - 1), iy & (mh - 1), out, outside ? &p.over_pn : nullptr);
}

// ---- the special functions ------------------------------------------------------------------
// layer: 0-3 NBG0-NBG3, 4 RBG0 (two bits each in SFPRMD and SFCCMD, one in SFSEL)
// SFPRMD: 0 the screen's priority; 1 its LSB from the character's (or bitmap's) special
// priority bit; 2 that bit only where the dot's colour code is one of the special function
// codes (SFSEL picks code A or B, SFCODE has them; codes are the dot's bits 3-1). SFCCMD alike
// for colour calculation, and 3: the colour's MSB.
static bool rbg_sf_code(int layer, const Pix& p) {
    uint8_t code = (uint8_t)(reg(0x26) >> ((reg(0x24) >> layer & 1) * 8));
    return !p.direct && (code >> ((p.dcc & 0xE) >> 1) & 1);
}

static uint8_t rbg_special_prio(int layer, uint8_t prio, const Pix& p) {
    int mode = reg(0xEA) >> (layer * 2) & 3;
    if (!mode) return prio;
    bool bit = mode == 1 ? p.spr : mode == 2 ? (p.spr && rbg_sf_code(layer, p)) : false;
    return (uint8_t)((prio & ~1) | (bit ? 1 : 0));
}

static bool rbg_special_cc(int layer, bool cc, const Pix& p) {
    switch (reg(0xEE) >> (layer * 2) & 3) {
    case 0: return cc;
    case 1: return cc && p.scc;
    case 2: return cc && p.scc && rbg_sf_code(layer, p);
    default: return cc && p.msb;
    }
}

