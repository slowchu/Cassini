// Offline snapshot deserialization only. Never constructs or invokes a Saturn core.
#define FMT_HEADER_ONLY
#define XXH_INLINE_ALL
#include <fmt/format.h>
#include <ymir/util/bit_ops.hpp>
#include <cereal/archives/portable_binary.hpp>
#include <serdes/cereal_savestate.hpp>
#include <nlohmann/json.hpp>
#include <xxh3.h>
#include <filesystem>
#include <fstream>
#include <iostream>
#include <memory>
#include <sstream>
#include <type_traits>
using JSON = nlohmann::json;
#include "named_dump.hpp"

std::string xxh128(const void* data, size_t size) {
    XXH128_canonical_t value{};
    XXH128_canonicalFromHash(&value, XXH128(data,size,0));
    std::string result;
    for(auto byte:value.digest) result += fmt::format("{:02X}",byte);
    return result;
}

int main(int argc, char** argv) {
    try {
        if (argc != 3) throw std::runtime_error("usage: read_state snapshot.ystate private-output.json");
        std::ifstream file(argv[1], std::ios::binary);
        if (!file) throw std::runtime_error("cannot open snapshot");
        if (std::filesystem::file_size(argv[1]) >= 64*1024*1024) throw std::runtime_error("snapshot exceeds preserved bridge bound");
        std::string bytes{std::istreambuf_iterator<char>(file), {}};
        // The preserved bridge emits portable-binary archive version 13.
        if (bytes.size() < 5 || static_cast<unsigned char>(bytes[0]) > 1)
            throw std::runtime_error("invalid portable archive header");
        uint32_t version = 0;
        for (unsigned i=0;i<4;i++) version |= uint32_t(uint8_t(bytes[1+i])) << (bytes[0] ? 8*i : 8*(3-i));
        if (version != ymir::savestate::kVersion) throw std::runtime_error("snapshot version differs from inspected serializer");
        auto state = std::make_unique<ymir::savestate::SaveState>();
        std::istringstream in(bytes, std::ios::binary);
        { cereal::PortableBinaryInputArchive archive(in); archive(*state); }
        if (in.peek() != std::char_traits<char>::eof()) throw std::runtime_error("trailing snapshot bytes");
        std::ostringstream roundtrip(std::ios::binary);
        { cereal::PortableBinaryOutputArchive archive(roundtrip); archive(*state); }
        if (roundtrip.str() != bytes) throw std::runtime_error("serializer roundtrip differs");
        JSON j = {{"schema","ymir-original-state-values-v1"},{"archive_version",version},
            {"snapshot_size",bytes.size()},{"roundtrip_byte_identical",true},
            {"snapshot_xxh128",xxh128(bytes.data(),bytes.size())},
            {"master",dump(state->msh2)},{"slave",dump(state->ssh2)},
            {"scu",dump(state->scu)},{"vdp",dump(state->vdp)},
            {"scheduler",dump(state->scheduler)},{"m68k",dump(state->scsp.m68k)},
            {"m68k_enabled",state->scsp.m68kEnabled},{"m68k_spillover_cycles",state->scsp.m68kSpilloverCycles},
            {"cdblock_lle",state->cdblockLLE},{"ipl_rom_hash",dump(state->system.iplRomHash)},
            {"disc_hash",dump(state->discHash)},{"slave_enabled",state->system.slaveSH2Enabled},
            {"video_standard",dump(state->system.videoStandard)},{"clock_speed",dump(state->system.clockSpeed)},
            {"master_spillover_cycles",state->msh2SpilloverCycles},{"slave_spillover_cycles",state->ssh2SpilloverCycles}};
        j["sh1"] = state->cdblockLLE ? dump(state->sh1) : JSON(nullptr);
        auto directory=std::filesystem::path(argv[2]).parent_path();
        auto ram = [&](const auto& data, const char* name) {
            std::ofstream f(directory/name,std::ios::binary);
            f.write(reinterpret_cast<const char*>(data.data()),data.size());
            if(!f) throw std::runtime_error("private RAM output failed");
            return JSON{{"file",name},{"size",data.size()},{"xxh128",xxh128(data.data(),data.size())}};
        };
        j["high_ram"]=ram(state->system.WRAMHigh,"high.bin");
        j["low_ram"]=ram(state->system.WRAMLow,"low.bin");
        std::ofstream out(argv[2], std::ios::binary);
        if (!out) throw std::runtime_error("cannot open private output");
        out << j.dump() << '\n';
        if (!out) throw std::runtime_error("output failed");
        std::cout << "deserialized; exact portable-serializer roundtrip; private output written\n";
        return 0;
    } catch(const std::exception& e) { std::cerr << e.what() << '\n'; return 1; }
}
