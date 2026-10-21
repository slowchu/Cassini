// Extended-colour second-image calculation adapted from Mednafen 1.32.1,
// src/ss/vdp2_render.cpp (SHA256 3871b2f6e7ba7dc80f134cc1de438b5d0a062bdb12d019a935b3c2f8d7ebf8a4).
// Copyright (C) 2016-2019 Mednafen Team
// SPDX-License-Identifier: GPL-2.0-or-later
//
// This program is free software; you can redistribute it and/or modify it under
// the terms of the GNU General Public License as published by the Free Software
// Foundation; either version 2 of the License, or (at your option) any later version.
// This program is distributed in the hope that it will be useful, but WITHOUT ANY
// WARRANTY; without even the implied warranty of MERCHANTABILITY or FITNESS FOR
// A PARTICULAR PURPOSE. See the GNU General Public License for more details.
// A copy of the GNU General Public License should accompany this program.
// Combined-project licence selection is GPL-3.0-only; existing MIT notices remain.
// No Mednafen common/framework headers are used.

static uint32_t excc_average(uint32_t a, uint32_t b) {
    return ((a + b) - ((a ^ b) & 0x010101)) >> 1;
}

// second/third are the visible images below the top. Inserting line colour makes
// them third/fourth. layer_cc is the raw screen-enable bit, not special pixel CC.
static Pix excc_second(Pix second, const Pix& third, const Pix& line,
                       bool insert_line, bool extended, unsigned cram) {
    if (insert_line) {
        Pix result = line; // retains CCRLB for second-image-ratio mode
        if (extended && line.layer_cc) {
            uint32_t below = second.rgb;
            if (cram == 0) {
                if (second.layer_cc) below = below >> 1 & 0x7F7F7F;
                result.rgb = excc_average(line.rgb, below);
            } else if (second.direct) {
                if (second.layer_cc && third.direct) below = excc_average(below, third.rgb);
                result.rgb = excc_average(line.rgb, below);
            }
        }
        return result;
    }
    if (extended && second.layer_cc && (cram == 0 || third.direct))
        second.rgb = excc_average(second.rgb, third.rgb);
    return second;
}
