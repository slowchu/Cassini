// Genuine native BUP file-entry transport using the pinned Ymir backup API.
// Does not launch an emulator or import CPU/RAM state. All output is private.
#include <ymir/sys/backup_ram.hpp>
#include <algorithm>
#include <cstdio>
#include <filesystem>
#include <fstream>
#include <iterator>
#include <stdexcept>
#include <string>
#include <utility>
#include <vector>

static void require(bool value, const char* why) {
    if (!value) throw std::runtime_error(why);
}
static uint32_t little32(const std::vector<uint8_t>& bytes, size_t at) {
    return uint32_t(bytes[at]) | (uint32_t(bytes[at+1]) << 8) |
        (uint32_t(bytes[at+2]) << 16) | (uint32_t(bytes[at+3]) << 24);
}
static std::string text(const std::vector<uint8_t>& bytes, size_t at, size_t count) {
    auto begin = bytes.begin() + at;
    auto end = std::find(begin, begin + count, uint8_t(0));
    require(end != begin + count, "native metadata has no terminating NUL");
    return std::string(begin, end);
}
static bool same(const ymir::bup::BackupFile& a, const ymir::bup::BackupFile& b) {
    return a.header.filename == b.header.filename && a.header.comment == b.header.comment &&
        a.header.language == b.header.language && a.header.date == b.header.date && a.data == b.data;
}
static bool same(const std::vector<ymir::bup::BackupFile>& files, const ymir::bup::BackupMemory& memory) {
    if (memory.List().size() != files.size()) return false;
    for (const auto& file : files) {
        auto exported = memory.Export(file.header.filename);
        if (!exported || !same(file, *exported)) return false;
    }
    return true;
}
int main(int argc, char** argv) {
    try {
        require(argc == 3, "usage: owner_bup_to_ymir input-native output-new-image");
        const std::filesystem::path source(argv[1]), target(argv[2]);
        require(!std::filesystem::exists(target), "output already exists");
        require(std::filesystem::file_size(source) >= 32 && std::filesystem::file_size(source) <= 32768,
            "invalid native file size");
        std::ifstream in(source, std::ios::binary);
        require(bool(in), "cannot open native save");
        std::vector<uint8_t> bytes((std::istreambuf_iterator<char>(in)), {});
        require(!in.bad(), "native save read failed");
        require(bytes.size() >= 32 && bytes.size() <= 32768, "native save changed size while reading");
        ymir::bup::BackupMemory memory;
        memory.CreateInMemory(ymir::bup::BackupMemorySize::_256Kbit);
        std::vector<ymir::bup::BackupFile> files;
        size_t payload_bytes = 0;
        for (size_t at = 0; at < bytes.size();) {
            require(bytes.size() - at >= 32, "truncated native save header");
            const uint32_t length = little32(bytes, at + 28);
            require(length <= bytes.size() - at - 32, "truncated native save payload");
            ymir::bup::BackupFile file;
            file.header.filename = text(bytes, at, 12);
            file.header.comment = text(bytes, at + 12, 11);
            file.header.language = static_cast<ymir::bup::Language>(bytes[at + 23]);
            file.header.date = little32(bytes, at + 24);
            file.data.assign(bytes.begin() + at + 32, bytes.begin() + at + 32 + length);
            require(!file.header.filename.empty(), "empty native save name");
            // Ymir exports metadata as fixed-width strings, including NUL padding.
            file.header.filename.resize(11, '\0');
            file.header.comment.resize(10, '\0');
            require(memory.Import(file, false) == ymir::bup::BackupFileImportResult::Imported, "import failed");
            files.push_back(std::move(file));
            payload_bytes += length;
            at += 32 + size_t(length);
        }
        require(same(files, memory), "in-memory save roundtrip differs");
        auto image = memory.ReadAll();
        require(image.size() == 32768 && memory.IsHeaderValid(), "invalid internal backup image");
        std::ofstream out(target, std::ios::binary);
        require(bool(out), "cannot create backup image");
        out.write(reinterpret_cast<const char*>(image.data()), image.size());
        out.close();
        require(bool(out), "backup image write failed");
        ymir::bup::BackupMemory reloaded;
        std::error_code error;
        require(reloaded.LoadFrom(target, true, error) == ymir::bup::BackupMemoryImageLoadResult::Success && !error,
            "backup image reload failed");
        require(reloaded.IsHeaderValid() && same(files, reloaded),
            "on-disk save roundtrip differs");
        std::printf("PASS native_bytes=%zu payload_bytes=%zu image_bytes=%zu files=%zu metadata_and_payload_exact=true\n",
            bytes.size(), payload_bytes, image.size(), files.size());
        return 0;
    } catch (const std::exception& error) {
        std::fprintf(stderr, "FAIL: %s\n", error.what());
        return 1;
    }
}
