// Same direct-source static-buffer seam as the retained compose benchmark.
#include "vdp1.cpp"
#include <cstdarg>
#include <cstdlib>
#include <cstring>
#include <stdexcept>

SaturnConfig g_cfg;
SH2Context g_master{}, g_slave{};
SH2Context* g_cpu=&g_master;
uint64_t sat_now() { return 0; }
void sat_trace(const char*, ...) {}
void sat_note(const char* fmt, ...) {
    va_list ap; va_start(ap,fmt); std::vfprintf(stderr,fmt,ap); va_end(ap); std::fputc('\n',stderr);
}
[[noreturn]] void sat_fatal(const char* fmt, ...) {
    va_list ap; va_start(ap,fmt); std::vfprintf(stderr,fmt,ap); va_end(ap); std::fputc('\n',stderr); std::abort();
}
[[noreturn]] void state_fail(const char*) { std::abort(); }
void scu_raise(int) { std::abort(); }
void scu_frame_event(int) { std::abort(); }

void inspect_load_vdp1(const uint8_t* vram,const uint8_t* regs,const uint8_t* fb) {
    std::memcpy(g_vdp1_vram,vram,sizeof g_vdp1_vram);
    for(size_t i=0;i<16;++i) g_reg[i]=(uint16_t)(regs[i*2]<<8|regs[i*2+1]);
    const int mode=g_reg[TVMR]&7;
    if(mode==2 || mode>=4) throw std::runtime_error("unsupported production TVMR mode");
    for(size_t i=0;i<512*256;++i) g_fb[0][i]=(uint16_t)(fb[i*2]<<8|fb[i*2+1]);
    g_draw_fb=1; g_target=g_fb[1]; g_show_ifb=false;
}
std::vector<uint32_t> inspect_production_order() {
    // Existing draw record supplies production CALL/RET semantics. Its throwaway target is not the supplied bank.
    g_cfg.interp=true; g_rec_cur=RecFrame{};
    draw();
    std::vector<uint32_t> result;
    for(const auto& d:g_rec_cur.draws) for(const auto& c:d.cmds) result.push_back(c.addr);
    g_cfg.interp=false;
    return result;
}
uint32_t inspect_order_terminal() { return uint32_t(g_reg[LOPR])*8; }
bool inspect_order_ended() { return (vw(inspect_order_terminal())&0x8000)!=0; }
int inspect_fb_width() { return fb_width(); }
int inspect_fb_height() { return fb_height(); }

bool inspect_controls_selfcheck() {
    std::vector<uint8_t> vram(524288), regs(32), fb(262144);
    auto word=[&](unsigned a,uint16_t v) { vram[a]=v>>8; vram[a+1]=(uint8_t)v; };
    word(0,0x2009); word(2,12); word(32,0x8000); word(96,0x2009); word(98,20); word(160,0x3009);
    inspect_load_vdp1(vram.data(),regs.data(),fb.data());
    if(inspect_production_order()!=std::vector<uint32_t>{0,96,160} || !inspect_order_ended()) return false;
    std::fill(vram.begin(),vram.end(),0); word(0,0x3009); word(32,0x8000);
    inspect_load_vdp1(vram.data(),regs.data(),fb.data());
    if(inspect_production_order()!=std::vector<uint32_t>{0} || !inspect_order_ended()) return false;
    std::fill(vram.begin(),vram.end(),0);
    word(4,8); word(6,0xFFFF); word(8,0x80); word(10,0x0101); word(32,0x8000);
    for(unsigned i=0;i<4;++i) vram[0x400+i]=0x11;
    word(0x7FFFA,0x801F); word(0x7FFE2,0x83E0);
    inspect_load_vdp1(vram.data(),regs.data(),fb.data()); g_sys_x=319; g_sys_y=223;
    inspect_production_order();
    return g_fb[1][0]==0x83E0 && tw(0xFFFF*8+8)==vw(0); // GPL raster reload aligns COLR; ordinary word helper wraps
}

