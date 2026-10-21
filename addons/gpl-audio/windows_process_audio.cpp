// GPL-3.0-only. External Windows process-loopback capture; no guest/runtime changes.
// API sequence: Microsoft's ApplicationLoopback sample, documented in the companion plan.
#define _WIN32_WINNT 0x0A00
#define NTDDI_VERSION 0x0A00000A
#define NOMINMAX
#include <windows.h>
#include <audioclient.h>
#include <mmdeviceapi.h>
#include <audioclientactivationparams.h>
#include <algorithm>
#include <array>
#include <cmath>
#include <cstdio>
#include <cstring>
#include <cwchar>
#include <filesystem>
#include <sstream>
#include <stdexcept>
#include <string>
#include <vector>

static constexpr UINT32 rate=44100, alignment=4;
static std::wstring private_root, allowed_binary;

struct Handle {
    HANDLE value=nullptr;
    ~Handle(){if(value && value!=INVALID_HANDLE_VALUE)CloseHandle(value);}
};
template<class T> struct Com {
    T* value=nullptr;
    ~Com(){if(value)value->Release();}
};
static void require(bool ok,const char* message){if(!ok)throw std::runtime_error(message);}
static void check(HRESULT hr,const char* message){
    if(FAILED(hr)){char text[256];std::snprintf(text,sizeof text,"%s: HRESULT 0x%08lX",message,(unsigned long)hr);throw std::runtime_error(text);}
}
static bool prefix(const std::wstring& path,const std::wstring& base){
    return path.size()>=base.size() && _wcsnicmp(path.c_str(),base.c_str(),base.size())==0;
}
static bool allowed_image(const std::wstring& path){
    return !allowed_binary.empty() && _wcsicmp(path.c_str(),allowed_binary.c_str())==0;
}
static std::wstring resolved(const std::wstring& path,bool directory=false){
    Handle file{CreateFileW(path.c_str(),FILE_READ_ATTRIBUTES,FILE_SHARE_READ|FILE_SHARE_WRITE|FILE_SHARE_DELETE,
        nullptr,OPEN_EXISTING,directory?FILE_FLAG_BACKUP_SEMANTICS:FILE_ATTRIBUTE_NORMAL,nullptr)};
    require(file.value!=INVALID_HANDLE_VALUE,"Cannot resolve existing path");
    std::vector<wchar_t> buffer(32768);
    DWORD n=GetFinalPathNameByHandleW(file.value,buffer.data(),(DWORD)buffer.size(),FILE_NAME_NORMALIZED|VOLUME_NAME_DOS);
    require(n>0 && n<buffer.size(),"Cannot resolve final path");
    std::wstring result(buffer.data(),n);
    if(prefix(result,L"\\\\?\\"))result.erase(0,4);
    return result;
}
static std::wstring private_prefix(const std::wstring& named){
    std::filesystem::path path(named);
    require(path.is_absolute() && !path.filename().empty() && path.filename()!=L"." && path.filename()!=L"..","Output prefix must be absolute");
    auto parent=resolved(path.parent_path().wstring(),true);
    require(_wcsicmp(parent.c_str(),private_root.c_str())==0 || prefix(parent+L"\\",private_root+L"\\"),"Raw audio and timestamps must remain under the explicit private root");
    return parent+L"\\"+path.filename().wstring();
}
static HANDLE target(DWORD pid,const std::wstring& expected){
    require(pid && pid!=GetCurrentProcessId(),"Target must be an explicit other process");
    auto image=resolved(expected);
    require(allowed_image(image),"Expected image differs from the explicit allowed binary");
    Handle process{OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION|SYNCHRONIZE,FALSE,pid)};
    require(process.value!=nullptr,"Cannot open target process");
    std::vector<wchar_t> actual(32768);DWORD n=(DWORD)actual.size();
    require(QueryFullProcessImageNameW(process.value,0,actual.data(),&n)!=0,"Cannot read target image");
    require(_wcsicmp(resolved(std::wstring(actual.data(),n)).c_str(),image.c_str())==0,"Target image does not equal expected Saturn image");
    require(WaitForSingleObject(process.value,0)==WAIT_TIMEOUT,"Target already exited");
    HANDLE result=process.value;process.value=nullptr;return result;
}
static double duration(const wchar_t* text){
    wchar_t* end=nullptr;double value=std::wcstod(text,&end);
    require(end!=text && *end==0 && std::isfinite(value) && value>0 && value<=600,"Duration must be finite and in (0,600] seconds");
    return value;
}
static DWORD remaining(ULONGLONG now,ULONGLONG deadline){
    return now>=deadline?0:(DWORD)std::min<ULONGLONG>(deadline-now,100);
}
static ULONGLONG filetime_now(){FILETIME t;GetSystemTimePreciseAsFileTime(&t);return ((ULONGLONG)t.dwHighDateTime<<32)|t.dwLowDateTime;}

class Activation final:public IActivateAudioInterfaceCompletionHandler,public IAgileObject {
    LONG references=1;
public:
    Handle done{CreateEventW(nullptr,TRUE,FALSE,nullptr)};
    Com<IAudioClient> client;
    HRESULT result=E_PENDING;
    HRESULT STDMETHODCALLTYPE QueryInterface(REFIID id,void** out) override {
        if(!out)return E_POINTER;*out=nullptr;
        if(id==__uuidof(IUnknown) || id==__uuidof(IActivateAudioInterfaceCompletionHandler))*out=static_cast<IActivateAudioInterfaceCompletionHandler*>(this);
        else if(id==__uuidof(IAgileObject))*out=static_cast<IAgileObject*>(this);
        else return E_NOINTERFACE;
        AddRef();return S_OK;
    }
    ULONG STDMETHODCALLTYPE AddRef() override{return (ULONG)InterlockedIncrement(&references);}
    ULONG STDMETHODCALLTYPE Release() override{LONG n=InterlockedDecrement(&references);if(!n)delete this;return (ULONG)n;}
    HRESULT STDMETHODCALLTYPE ActivateCompleted(IActivateAudioInterfaceAsyncOperation* op) override {
        Com<IUnknown> audio;HRESULT activated=E_UNEXPECTED;
        result=op->GetActivateResult(&activated,&audio.value);
        if(SUCCEEDED(result))result=activated;
        if(SUCCEEDED(result))result=audio.value?audio.value->QueryInterface(__uuidof(IAudioClient),(void**)&client.value):E_POINTER;
        SetEvent(done.value);return S_OK;
    }
};

static void write_all(HANDLE file,const BYTE* data,size_t bytes){
    for(size_t at=0;at<bytes;){
        DWORD count=(DWORD)std::min<size_t>(bytes-at,1024*1024),wrote=0;
        require(WriteFile(file,data+at,count,&wrote,nullptr) && wrote==count,"Output write failed");at+=wrote;
    }
}
static void write_new(const std::wstring& path,const BYTE* data,size_t bytes){
    Handle file{CreateFileW(path.c_str(),GENERIC_WRITE,0,nullptr,CREATE_NEW,FILE_ATTRIBUTE_NORMAL,nullptr)};
    require(file.value!=INVALID_HANDLE_VALUE,"Cannot create new output; existing evidence is preserved");
    write_all(file.value,data,bytes);
}
static std::array<BYTE,44> wav_header(size_t bytes){
    require(bytes<=0xFFFFFFFFu-36,"WAV size overflow");
    std::array<BYTE,44> h{};
    auto u16=[&](size_t at,UINT16 n){h[at]=(BYTE)n;h[at+1]=(BYTE)(n>>8);};
    auto u32=[&](size_t at,UINT32 n){for(unsigned i=0;i<4;++i)h[at+i]=(BYTE)(n>>(i*8));};
    std::memcpy(h.data(),"RIFF",4);u32(4,(UINT32)bytes+36);std::memcpy(h.data()+8,"WAVEfmt ",8);
    u32(16,16);u16(20,1);u16(22,2);u32(24,rate);u32(28,rate*alignment);u16(32,alignment);u16(34,16);
    std::memcpy(h.data()+36,"data",4);u32(40,(UINT32)bytes);return h;
}
static void write_wav(const std::wstring& path,const std::vector<BYTE>& data){
    auto header=wav_header(data.size());
    Handle file{CreateFileW(path.c_str(),GENERIC_WRITE,0,nullptr,CREATE_NEW,FILE_ATTRIBUTE_NORMAL,nullptr)};
    require(file.value!=INVALID_HANDLE_VALUE,"Cannot create new WAV; existing evidence is preserved");
    write_all(file.value,header.data(),header.size());write_all(file.value,data.data(),data.size());
}
struct Packet {UINT64 file_frame,device_frame,qpc_100ns,observed_filetime;LONGLONG observed_qpc;UINT32 frames;DWORD flags;};

static int capture(DWORD pid,const std::wstring& expected,double seconds,const std::wstring& output){
    const ULONGLONG deadline=GetTickCount64()+(ULONGLONG)std::ceil(seconds*1000);
    Handle process{target(pid,expected)};auto base=private_prefix(output);
    for(auto suffix:{L".wav",L".csv"})require(GetFileAttributesW((base+suffix).c_str())==INVALID_FILE_ATTRIBUTES,"Output already exists");
    check(CoInitializeEx(nullptr,COINIT_MULTITHREADED),"COM MTA initialization");
    struct Apartment{~Apartment(){CoUninitialize();}} apartment;
    Com<Activation> activation;activation.value=new Activation;
    require(activation.value->done.value!=nullptr,"Activation event creation failed");
    AUDIOCLIENT_ACTIVATION_PARAMS parameters{};
    parameters.ActivationType=AUDIOCLIENT_ACTIVATION_TYPE_PROCESS_LOOPBACK;
    parameters.ProcessLoopbackParams.TargetProcessId=pid;
    parameters.ProcessLoopbackParams.ProcessLoopbackMode=PROCESS_LOOPBACK_MODE_INCLUDE_TARGET_PROCESS_TREE;
    PROPVARIANT blob{};blob.vt=VT_BLOB;blob.blob.cbSize=sizeof parameters;blob.blob.pBlobData=(BYTE*)&parameters;
    Com<IActivateAudioInterfaceAsyncOperation> operation;
    check(ActivateAudioInterfaceAsync(VIRTUAL_AUDIO_DEVICE_PROCESS_LOOPBACK,__uuidof(IAudioClient),&blob,activation.value,&operation.value),"Process-only loopback activation; no desktop fallback");
    ULONGLONG activation_limit=std::min(deadline,GetTickCount64()+10000);
    DWORD wait=WaitForSingleObject(activation.value->done.value,(DWORD)std::max<ULONGLONG>(1,activation_limit-std::min(GetTickCount64(),activation_limit)));
    if(wait!=WAIT_OBJECT_0){
        // The OS may still own the callback: end this process rather than freeing a live callback.
        std::fprintf(stderr,"Process-loopback activation did not complete within its bounded wait; capture unavailable\n");std::fflush(stderr);ExitProcess(1);
    }
    check(activation.value->result,"Process-loopback activation result; no desktop fallback");
    require(GetTickCount64()<deadline,"Duration expired during activation");
    auto client=activation.value->client.value;
    WAVEFORMATEX format{WAVE_FORMAT_PCM,2,rate,rate*alignment,alignment,16,0};
    check(client->Initialize(AUDCLNT_SHAREMODE_SHARED,AUDCLNT_STREAMFLAGS_LOOPBACK|AUDCLNT_STREAMFLAGS_EVENTCALLBACK|AUDCLNT_STREAMFLAGS_AUTOCONVERTPCM,0,0,&format,nullptr),"Process-loopback format initialization");
    Handle samples{CreateEventW(nullptr,FALSE,FALSE,nullptr)};require(samples.value!=nullptr,"Sample event creation failed");
    check(client->SetEventHandle(samples.value),"Sample event registration");
    Com<IAudioCaptureClient> reader;check(client->GetService(__uuidof(IAudioCaptureClient),(void**)&reader.value),"Capture service");
    const size_t byte_limit=(size_t)std::ceil((seconds+1)*rate)*alignment;
    const size_t packet_limit=(size_t)std::ceil(seconds*1000)+1000;
    std::vector<BYTE> data;data.reserve(byte_limit);std::vector<Packet> packets;packets.reserve(packet_limit);
    LARGE_INTEGER frequency,started;QueryPerformanceFrequency(&frequency);QueryPerformanceCounter(&started);ULONGLONG started_filetime=filetime_now();
    check(client->Start(),"Capture start");
    struct Stop{IAudioClient* value;~Stop(){if(value)value->Stop();}} stop{client};
    std::fprintf(stderr,"capture-ready pid=%lu start_qpc=%lld start_filetime_100ns=%llu\n",(unsigned long)pid,started.QuadPart,started_filetime);std::fflush(stderr);
    HANDLE handles[]={process.value,samples.value};bool exited=false;
    while(GetTickCount64()<deadline){
        DWORD ready=WaitForMultipleObjects(2,handles,FALSE,remaining(GetTickCount64(),deadline));
        if(ready==WAIT_OBJECT_0){exited=true;break;}
        require(ready==WAIT_OBJECT_0+1 || ready==WAIT_TIMEOUT,"Capture wait failed");
        if(ready==WAIT_TIMEOUT)continue;
        for(UINT32 next=0;GetTickCount64()<deadline;){
            check(reader.value->GetNextPacketSize(&next),"Packet size");if(!next)break;
            BYTE* bytes=nullptr;UINT32 frames=0;DWORD flags=0;UINT64 device=0,qpc=0;
            check(reader.value->GetBuffer(&bytes,&frames,&flags,&device,&qpc),"Capture packet");
            const size_t count=(size_t)frames*alignment;
            LARGE_INTEGER observed;QueryPerformanceCounter(&observed);ULONGLONG observed_filetime=filetime_now();
            bool bounded=count<=byte_limit-data.size() && packets.size()<packet_limit;
            if(bounded){
                packets.push_back({data.size()/alignment,device,qpc,observed_filetime,observed.QuadPart,frames,flags});
                size_t at=data.size();data.resize(at+count);
                if(flags&AUDCLNT_BUFFERFLAGS_SILENT)std::memset(data.data()+at,0,count);
                else if(bytes)std::memcpy(data.data()+at,bytes,count);
                else bounded=false;
            }
            check(reader.value->ReleaseBuffer(frames),"Release packet");require(bounded,"Capture exceeded bounded storage or returned invalid data");
        }
    }
    check(client->Stop(),"Capture stop");stop.value=nullptr;
    write_wav(base+L".wav",data);
    std::ostringstream csv;csv<<"file_frame,frames,flags,device_frame,qpc_100ns,observed_qpc,observed_filetime_100ns\n";
    size_t discontinuities=0,timestamp_errors=0;
    for(const auto& p:packets){
        csv<<p.file_frame<<','<<p.frames<<','<<p.flags<<','<<p.device_frame<<','<<p.qpc_100ns<<','<<p.observed_qpc<<','<<p.observed_filetime<<'\n';
        discontinuities+=(p.flags&AUDCLNT_BUFFERFLAGS_DATA_DISCONTINUITY)!=0;timestamp_errors+=(p.flags&AUDCLNT_BUFFERFLAGS_TIMESTAMP_ERROR)!=0;
    }
    auto text=csv.str();write_new(base+L".csv",(const BYTE*)text.data(),text.size());
    std::printf("{\"pid\":%lu,\"include_only_process_tree\":true,\"rate\":%u,\"frames\":%zu,\"packets\":%zu,\"start_qpc\":%lld,\"qpc_frequency\":%lld,\"start_filetime_100ns\":%llu,\"target_exited\":%s,\"discontinuity_packets\":%zu,\"timestamp_error_packets\":%zu}\n",(unsigned long)pid,rate,data.size()/alignment,packets.size(),started.QuadPart,frequency.QuadPart,started_filetime,exited?"true":"false",discontinuities,timestamp_errors);
    require(!data.empty(),"No samples captured; audio qualification unavailable");return 0;
}

static int self_check(const std::wstring& named){
    require(duration(L"600")==600 && duration(L"0.01")==0.01,"Valid durations rejected");
    for(auto text:{L"0",L"601",L"nan",L"inf",L"1tail",L"-1"}){
        bool rejected=false;try{duration(text);}catch(...){rejected=true;}require(rejected,"Invalid duration accepted");
    }
    require(remaining(100,100)==0 && remaining(101,100)==0 && remaining(1,1000)==100 && remaining(990,1000)==10,"Timeout bounds failed");
    require(allowed_image(allowed_binary),"Explicit image rejected");
    require(!allowed_image(allowed_binary+L".other"),"Other image accepted");
    bool rejected=false;try{target(GetCurrentProcessId(),allowed_binary);}catch(...){rejected=true;}require(rejected,"Self-target accepted");
    auto base=private_prefix(named);std::vector<BYTE> data{1,0,2,0,3,0,4,0};write_wav(base+L".wav",data);
    rejected=false;try{write_wav(base+L".wav",data);}catch(...){rejected=true;}require(rejected,"Existing output overwritten");
    auto header=wav_header(data.size());require(header[4]==44 && header[40]==8 && header[22]==2 && header[34]==16,"Synthetic WAV header invalid");
    std::puts("Synthetic WAV, duration/timeout bounds, exact image/self-target checks and original-output preservation passed; no audio interface activated.");return 0;
}

int wmain(int argc,wchar_t** argv){
    try{
        if(argc==4 && std::wcscmp(argv[1],L"--self-check")==0){
            private_root=resolved(argv[2],true);allowed_binary=resolved(argv[0]);return self_check(argv[3]);
        }
        require(argc==6,"usage: windows_process_audio PID EXPECTED_IMAGE SECONDS PRIVATE_ROOT OUTPUT_PREFIX; or --self-check PRIVATE_ROOT PREFIX");
        private_root=resolved(argv[4],true);allowed_binary=resolved(argv[2]);
        wchar_t* end=nullptr;unsigned long long pid=std::wcstoull(argv[1],&end,10);
        require(end!=argv[1] && !*end && pid>0 && pid<=0xFFFFFFFFu,"Invalid target PID");
        return capture((DWORD)pid,argv[2],duration(argv[3]),argv[5]);
    }catch(const std::exception& e){std::fprintf(stderr,"process-only capture failed: %s\n",e.what());return 1;}
}
