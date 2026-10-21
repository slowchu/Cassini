// All sampling, rotation decoding and colour composition below are production code.
#include "vdp2.cpp"
#include <array>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <iterator>
#include <stdexcept>

void inspect_load_vdp1(const uint8_t*,const uint8_t*,const uint8_t*);
std::vector<uint32_t> inspect_production_order();
uint32_t inspect_order_terminal();
bool inspect_order_ended();
bool inspect_controls_selfcheck();
int inspect_fb_width();
int inspect_fb_height();
namespace fs=std::filesystem;

static std::vector<uint8_t> load(const fs::path& p,size_t n) {
    std::ifstream in(p,std::ios::binary);
    std::vector<uint8_t> v((std::istreambuf_iterator<char>(in)),{});
    if(!in || v.size()!=n) throw std::runtime_error("fixture size/read mismatch: "+p.string());
    return v;
}
struct Fields {
    std::ostream& o; bool comma=false;
    template<class T> void value(const char* key,T v) {
        if(comma) o<<','; comma=true; o<<'"'<<key<<"\":"<<+v;
    }
    template<class T> void array(const char* key,const T* p,size_t n) {
        if(comma) o<<','; comma=true; o<<'"'<<key<<"\":[";
        for(size_t i=0;i<n;++i) { if(i) o<<','; o<<+p[i]; } o<<']';
    }
};
static void nbg_json(std::ostream& o,const Nbg& s) {
    o<<'{'; Fields j{o};
    j.value("enabled",s.on); j.value("opaque",s.opaque); j.value("bitmap",s.bitmap);
    j.value("cell2x2",s.cell2x2); j.value("one_word_pattern",s.word1); j.value("character_supplement_mode",s.cnsm);
    j.value("colour_mode",s.colors); j.value("pattern_supplement",s.supp);
    j.value("plane_w_pages",s.plane_w); j.value("plane_h_pages",s.plane_h); j.array("plane_addresses",s.plane,4);
    j.value("page_bytes",s.page_bytes); j.value("bitmap_width",s.bm_w); j.value("bitmap_height",s.bm_h);
    j.value("bitmap_address",s.bm_addr); j.value("bitmap_palette",s.bm_pal);
    j.value("bitmap_special_priority",s.bm_spr); j.value("bitmap_special_cc",s.bm_scc);
    j.value("colour_ram_offset_entries",s.caos); j.value("scroll_x_fixed8",s.sx); j.value("scroll_y_fixed8",s.sy);
    j.value("zoom_dx_fixed8",s.dx); j.value("zoom_dy_fixed8",s.dy);
    j.value("scroll_control",s.scroll_ctl); j.value("line_interval",s.scroll_interval); j.value("cell_stride",s.cell_stride);
    j.value("line_table_address",s.line_table); j.value("cell_table_address",s.cell_table);
    j.value("priority",s.prio); j.value("cc_ratio",s.ratio); j.value("colour_calculation",s.cc);
    j.value("colour_offset",s.offset); j.value("colour_offset_b",s.offset_b); o<<'}';
}
static void rotation_json(std::ostream& o,const Rbg& r) {
    o<<"{\"enabled\":"<<r.on<<",\"mode\":"<<r.mode<<",\"priority\":"<<+r.prio<<",\"ratio\":"<<+r.ratio
     <<",\"colour_calculation\":"<<r.cc<<",\"colour_offset\":"<<r.offset<<",\"colour_offset_b\":"<<r.offset_b
     <<",\"coefficients_in_cram\":"<<r.crkte<<",\"per_dot\":"<<r.perdot<<",\"coefficient_banks\":[";
    for(int i=0;i<4;++i) { if(i) o<<','; o<<r.bank_coef[i]; }
    o<<"],\"parameters\":[";
    for(int i=0;i<2;++i) {
        if(i) o<<',';
        const RotParam& p=r.par[i]; o<<"{\"characters\":"; nbg_json(o,p.chr); o<<','; Fields j{o};
        j.array("plane_addresses",p.plane,16); j.value("xst",p.xst); j.value("yst",p.yst); j.value("zst",p.zst);
        j.value("dxst",p.dxst); j.value("dyst",p.dyst); j.value("dx",p.dx); j.value("dy",p.dy); j.array("matrix",p.m,6);
        j.value("px",p.px); j.value("py",p.py); j.value("pz",p.pz); j.value("cx",p.cx); j.value("cy",p.cy); j.value("cz",p.cz);
        j.value("mx",p.mx); j.value("my",p.my); j.value("kx",p.kx); j.value("ky",p.ky);
        j.value("ka0",p.ka0); j.value("dkast",p.dkast); j.value("dkax",p.dkax); j.value("coefficient",p.coef);
        j.value("coefficient_word",p.coef1w); j.value("coefficient_mode",p.coef_mode);
        j.value("coefficient_line_colour",p.coefficient_line_color); j.value("screen_over",p.over); j.value("over_pattern",p.over_pn); o<<'}';
    } o<<"]}";
}
static void setreg(unsigned a,uint16_t v) { g_vdp2_regs[a]=v>>8; g_vdp2_regs[a+1]=(uint8_t)v; }
static void window_json(std::ostream& o,uint8_t ctl) {
    o<<"{\"window0_enabled\":"<<bool(ctl&2)<<",\"window0_outside\":"<<bool(ctl&1)
     <<",\"window1_enabled\":"<<bool(ctl&8)<<",\"window1_outside\":"<<bool(ctl&4)
     <<",\"and_operation\":"<<bool(ctl&0x80)<<'}';
}
static void compose_png(const fs::path& file) {
    Frame f; vdp2_compose(f,-1); // independent whole static reconstruction; no previous-field history supplied
    if(f.w<=0 || f.h<=0 || f.px.size()!=size_t(f.w)*f.h) throw std::runtime_error("invalid production output");
    write_png(file.string(),f);
}
int main(int argc,char** argv) {
    try {
        if(argc==2 && std::string(argv[1])=="--self-check") {
            if(!inspect_controls_selfcheck()) throw std::runtime_error("production CALL/RET/LUT fixture failed");
            std::cout<<"{\"passed\":true,\"production_control_cases\":3}\n"; return 0;
        }
        if(argc!=3) return 2;
        const fs::path input=argv[1], output=argv[2];
        if(!fs::is_directory(input) || !fs::create_directory(output)) throw std::runtime_error("fresh output directory required");
        const auto vram=load(input/"vdp1_vram.bin",524288), regs1=load(input/"vdp1_registers.bin",32), fb=load(input/"framebuffer.bin",262144);
        const auto vram2=load(input/"vdp2_vram.bin",524288), regs2=load(input/"vdp2_registers.bin",512), cram2=load(input/"cram.bin",4096);
        std::memcpy(g_vdp2_vram,vram2.data(),vram2.size()); std::memcpy(g_vdp2_cram,cram2.data(),cram2.size());
        std::memcpy(g_vdp2_regs,regs2.data(),regs2.size()); inspect_load_vdp1(vram.data(),regs1.data(),fb.data());
        const auto order=inspect_production_order(); // existing producer; not a second walker
        const auto terminal=inspect_order_terminal(); const bool ended=inspect_order_ended();
        inspect_load_vdp1(vram.data(),regs1.data(),fb.data()); // restore capture after throwaway order recording
        std::ofstream meta(output/"production.json");
        meta<<"{\"schema\":\"graphics-inspector-production-v1\",\"command_addresses\":[";
        for(size_t i=0;i<order.size();++i) { if(i) meta<<','; meta<<order[i]; }
        meta<<"],\"command_terminal_address\":"<<terminal<<",\"command_order_terminated\":"<<ended
            <<",\"configuration_decode_includes_disabled\":true,\"global\":{\"display_enabled\":"<<bool(reg(0)&0x8000)
            <<",\"horizontal_resolution\":"<<(reg(0)&7)<<",\"vertical_resolution\":"<<((reg(0)>>4)&3)
            <<",\"interlace_mode\":"<<((reg(0)>>6)&3)<<",\"cram_mode\":"<<cram_mode()
            <<",\"cc_add\":"<<bool(reg(0xEC)&0x100)<<",\"cc_second_ratio\":"<<bool(reg(0xEC)&0x200)
            <<",\"extended_cc_requested\":"<<bool(reg(0xEC)&0x400)<<",\"rotation_table_base\":"
            <<((((uint32_t)(reg(0xBC)&7)<<16|(reg(0xBE)&0xFFFE))*2)&0xFFF7C)<<"},\"normal_layers\":{";
        const auto bgon=reg(0x20);
        for(int n=0;n<4;++n) {
            if(n) meta<<','; meta<<"\"NBG"<<n<<"\":";
            setreg(0x20,bgon|(1u<<n)); Nbg s; setup_nbg(n,s); s.on=(bgon>>n)&1;
            nbg_json(meta,s);
        }
        setreg(0x20,bgon|0x10); Rbg r; setup_rbg(r,true); r.on=bgon&0x10;
        meta<<"},\"rotation\":"; rotation_json(meta,r); setreg(0x20,bgon);
        meta<<",\"windows\":{\"layer_controls\":["<<reg(0xD0)<<','<<reg(0xD2)<<','<<reg(0xD4)<<','<<reg(0xD6)
            <<"],\"rectangles\":[";
        for(int w=0;w<2;++w) { if(w) meta<<','; meta<<'['; for(int k=0;k<4;++k) { if(k) meta<<','; meta<<reg(0xC0+w*8+k*2); } meta<<']'; }
        meta<<"],\"line_table_words\":["<<reg(0xD8)<<','<<reg(0xDA)<<','<<reg(0xDC)<<','<<reg(0xDE)<<"],\"decoded_controls\":[";
        const uint8_t ctls[]={(uint8_t)reg(0xD0),(uint8_t)(reg(0xD0)>>8),(uint8_t)reg(0xD2),(uint8_t)(reg(0xD2)>>8),
                             (uint8_t)reg(0xD4),(uint8_t)(reg(0xD4)>>8),(uint8_t)(reg(0xD6)>>8),(uint8_t)reg(0xD6)};
        for(int i=0;i<8;++i) { if(i) meta<<','; window_json(meta,ctls[i]); }
        meta<<"],\"control_order\":[\"NBG0\",\"NBG1\",\"NBG2\",\"NBG3\",\"RBG0\",\"sprite\",\"colour-calculation\",\"rotation-parameter\"],\"decoded_lines\":[";
        const bool dd=(reg(0)>>6&3)==3;
        for(int y=0;y<vdp2_lines()*(dd?2:1);++y) {
            if(y) meta<<','; meta<<'[';
            for(int w=0;w<2;++w) { if(w) meta<<','; const auto win=window_line(w,y,dd); meta<<'['<<win.on_line<<','<<win.xs<<','<<win.xe<<']'; }
            meta<<']';
        }
        meta<<"]},\"unavailable\":["
            <<"{\"layer\":\"RBG1\",\"requested\":"<<bool(bgon&0x20)<<",\"reason\":\"not implemented by production compositor\"},"
            <<"{\"feature\":\"mosaic\",\"requested\":"<<bool(reg(0x22)&0xF)<<",\"reason\":\"not implemented by production compositor\"},"
            <<"{\"feature\":\"sprite-window\",\"requested\":"<<bool((reg(0xD0)|reg(0xD2)|reg(0xD4)|reg(0xD6))&0x2020)
            <<",\"spctl_enable\":"<<bool(reg(0xE0)&0x10)<<",\"reason\":\"not implemented by production compositor\"}]}";
        meta.close(); if(!meta) throw std::runtime_error("metadata output failed");
        compose_png(output/"composite.png");
        Frame rawfb; rawfb.w=inspect_fb_width(); rawfb.h=inspect_fb_height(); rawfb.px.resize(size_t(rawfb.w)*rawfb.h);
        for(int y=0;y<rawfb.h;++y) for(int x=0;x<rawfb.w;++x) {
            Pix p{}; const auto kind=sprite_pixel(vdp1_dot(x,y,false),p);
            rawfb.px[size_t(y)*rawfb.w+x]=kind==S_DOT ? p.rgb : 0;
        }
        write_png((output/"vdp1-framebuffer.png").string(),rawfb);
        setreg(0x20,0); compose_png(output/"vdp1-layer.png");
        for(int n=0;n<5;++n) {
            std::memcpy(g_vdp2_regs,regs2.data(),regs2.size());
            setreg(0x20,(uint16_t)(bgon & ((1u<<n)|(1u<<(n+8)))));
            for(unsigned a=0xF0;a<=0xF6;a+=2) setreg(a,0); // omit sprite layer, including shadow priority
            compose_png(output/(n==4 ? "RBG0.png" : "NBG"+std::to_string(n)+".png"));
        }
        std::cout<<"{\"passed\":true,\"production_composes\":7,\"snapshot_only\":true}\n";
        return 0;
    } catch(const std::exception& e) { std::cerr<<e.what()<<'\n'; return 1; }
}
