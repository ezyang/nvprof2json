import sqlite3
import argparse
import enum
import json
import subprocess
import os
import sys
import copy

def main():
    parser = argparse.ArgumentParser(description='Convert nvprof output to Google Event Trace compatible JSON.')
    parser.add_argument('filename')
    args = parser.parse_args()

    conn = sqlite3.connect(args.filename)
    conn.row_factory = sqlite3.Row

    strings = {}
    for r in conn.execute("SELECT _id_ as id, value FROM StringTable"):
        strings[r["id"]] = demangle(r["value"])

    traceEvents = []

    """
    _id_: 11625
    cbid: 17
    start: 1496933427584362152
    end: 1496933427584362435
    processId: 1317533
    threadId: 1142654784
    correlationId: 13119
    returnValue: 0
    """
    for row in conn.execute("SELECT * FROM CUPTI_ACTIVITY_KIND_RUNTIME"):
        #eprintRow(row)
        try:
            cbid = Cbids(row["cbid"]).name
        except ValueError:
            cbid = str(row["cbid"])
            eprint("Unrecognized cbid {}".format(cbid))
        event = {
                "name": cbid,
                "ph": "X", # Complete Event (Begin + End event)
                "cat": "cuda",
                "ts": munge_time(row["start"]),
                "dur": munge_time(row["end"] - row["start"]),
                "tid": "Thread {}: Runtime API".format(row["threadId"]),
                "pid": "[{}] Process".format(row["processId"]),
                "args": {
                    # TODO: More
                    },
                }
        traceEvents.append(event)

    # TODO DRIVER

    """
    _id_: 1
    flags: 2
    timestamp: 1496844806028263989
    id: 1
    objectKind: 2
    objectId: b'\xe5\xc0\x16\x00@\xe7\x10J\x00\x00\x00\x00'
    name: 3
    domain: 0
    """
    for row in conn.execute(" ".join([
            "SELECT",
            ",".join([
                "start.name AS name",
                "start.timestamp AS start_time",
                "end.timestamp AS end_time"
            ]),
            "FROM",
            "(SELECT * FROM CUPTI_ACTIVITY_KIND_MARKER WHERE name != 0) AS start",
            "LEFT JOIN",
            "(SELECT * FROM CUPTI_ACTIVITY_KIND_MARKER WHERE name = 0) AS end",
            "ON start.id = end.id"])):
        event = {
                "name": strings[row["name"]],
                "cat": "cuda",
                "ts": munge_time(row["start_time"]),
                # Weirdly, these don't seem to be associated with a
                # CPU/GPU.  I guess there's no CUDA Context available
                # when you run these, so it makes sense.  But nvvp
                # associates these with a GPU strangely enough
                "tid": "Markers and Ranges",
                "pid": "Markers and Ranges",
                # TODO: NO COLORS FOR YOU (probably have to parse
                # objectId)
                "args": {
                    # TODO: More
                    },
                }
        if row["end_time"] is None:
            event["ph"] = "I"
        else:
            event["ph"] = "X"
            event["dur"] = munge_time(row["end_time"] - row["start_time"])
        traceEvents.append(event)

    """
    _id_: 1
    copyKind: 1
    srcKind: 1
    dstKind: 3
    flags: 0
    bytes: 7436640
    start: 1496933426915778221
    end: 1496933426916558424
    deviceId: 0
    contextId: 1
    streamId: 7
    correlationId: 809
    runtimeCorrelationId: 0
    """
    for row in conn.execute("SELECT * FROM CUPTI_ACTIVITY_KIND_MEMCPY"):
        # copyKind:
        #   1 - Memcpy HtoD
        #   2 - Memcpy DtoH
        #   8 - Memcpy DtoD
        # flags: ???
        #   0 - Sync
        #   1 - Async
        # srcKind/dstKind
        #   1 - Pageable
        #   2 - Page-locked ???
        #   3 - Device
        #eprintRow(row)
        if row["copyKind"] == 1:
            copyKind = "HtoD"
        elif row["copyKind"] == 2:
            copyKind = "DtoH"
        elif row["copyKind"] == 8:
            copyKind = "DtoD"
        else:
            copyKind = str(row["copyKind"])
        if row["flags"] == 0:
            flags = "sync"
        elif row["flags"] == 1:
            flags = "async"
        else:
            flags = str(row["flags"])
        event = {
                "name": "Memcpy {} [{}]".format(copyKind, flags),
                "ph": "X", # Complete Event (Begin + End event)
                "cat": "cuda",
                "ts": munge_time(row["start"]),
                "dur": munge_time(row["end"] - row["start"]),
                "tid": "MemCpy ({})".format(copyKind),
                # TODO: lookup GPU name.  This is tored in
                # CUPTI_ACTIVITY_KIND_DEVICE
                "pid": "[{}:{}] Overview".format(row["deviceId"], row["contextId"]),
                "args": {
                    "Size": sizeof_fmt(row["bytes"]),
                    # TODO: More
                    },
                }
        traceEvents.append(event)

    # name: index into StringTable
    # What is thed difference between end and completed?
    """
    _id_: 1
    cacheConfig: b'\x00'
    sharedMemoryConfig: 1
    registersPerThread: 32
    partitionedGlobalCacheRequested: 2
    partitionedGlobalCacheExecuted: 2
    start: 1496844806032514222
    end: 1496844806032531694
    completed: 1496844806032531694
    deviceId: 0
    contextId: 1
    streamId: 7
    gridX: 57
    gridY: 1
    gridZ: 1
    blockX: 128
    blockY: 1
    blockZ: 1
    staticSharedMemory: 0
    dynamicSharedMemory: 0
    localMemoryPerThread: 0
    localMemoryTotal: 78643200
    correlationId: 487
    gridId: 669
    name: 5
    """
    for row in conn.execute("SELECT * FROM CUPTI_ACTIVITY_KIND_CONCURRENT_KERNEL"):
        #eprint(strings[row["name"]])
        #eprintRow(row)
        event = {
                "name": strings[row["name"]],
                "ph": "X", # Complete Event (Begin + End event)
                "cat": "cuda",
                "ts": munge_time(row["start"]),
                "dur": munge_time(row["end"] - row["start"]),
                "tid": "Compute",
                # TODO: lookup GPU name
                "pid": "[{}:{}] Overview".format(row["deviceId"], row["contextId"]),
                "args": {
                    "Grid size": "[ {}, {}, {} ]".format(row["gridX"], row["gridY"], row["gridZ"]),
                    "Block size": "[ {}, {}, {} ]".format(row["blockX"], row["blockY"], row["blockZ"]),
                    # TODO: More
                    },
                }
        alt_event = copy.deepcopy(event)
        alt_event["tid"] = alt_event["name"]
        alt_event["pid"] = "[{}:{}] Compute".format(row["deviceId"], row["contextId"])
        traceEvents.append(event)
        traceEvents.append(alt_event)


    json.dump(traceEvents, sys.stdout)
    print()

def munge_time(t):
    """Take a time from nvprof and convert it into a chrome://tracing time."""
    # For strict correctness, divide by 1000, but this reduces accuracy.
    return t # / 1000.

def demangle(name):
    """Demangle a C++ identifier using c++filt"""
    # TODO: create the process only once.
    # Fortunately, this doesn't seem to be a bottleneck ATM.
    try:
        with open(os.devnull, 'w') as devnull:
            return subprocess.check_output(['c++filt', '-n', name], stderr=devnull).rstrip().decode("ascii")
    except subprocess.CalledProcessError:
        return name


class Cbids(enum.IntEnum):
    INVALID = 0
    cudaDriverGetVersion = 1
    cudaRuntimeGetVersion = 2
    cudaGetDeviceCount = 3
    cudaGetDeviceProperties = 4
    cudaChooseDevice = 5
    cudaGetChannelDesc = 6
    cudaCreateChannelDesc = 7
    cudaConfigureCall = 8
    cudaSetupArgument = 9
    cudaGetLastError = 10
    cudaPeekAtLastError = 11
    cudaGetErrorString = 12
    cudaLaunch = 13
    cudaFuncSetCacheConfig = 14
    cudaFuncGetAttributes = 15
    cudaSetDevice = 16
    cudaGetDevice = 17
    cudaSetValidDevices = 18
    cudaSetDeviceFlags = 19
    cudaMalloc = 20
    cudaMallocPitch = 21
    cudaFree = 22
    cudaMallocArray = 23
    cudaFreeArray = 24
    cudaMallocHost = 25
    cudaFreeHost = 26
    cudaHostAlloc = 27
    cudaHostGetDevicePointer = 28
    cudaHostGetFlags = 29
    cudaMemGetInfo = 30
    cudaMemcpy = 31
    cudaMemcpy2D = 32
    cudaMemcpyToArray = 33
    cudaMemcpy2DToArray = 34
    cudaMemcpyFromArray = 35
    cudaMemcpy2DFromArray = 36
    cudaMemcpyArrayToArray = 37
    cudaMemcpy2DArrayToArray = 38
    cudaMemcpyToSymbol = 39
    cudaMemcpyFromSymbol = 40
    cudaMemcpyAsync = 41
    cudaMemcpyToArrayAsync = 42
    cudaMemcpyFromArrayAsync = 43
    cudaMemcpy2DAsync = 44
    cudaMemcpy2DToArrayAsync = 45
    cudaMemcpy2DFromArrayAsync = 46
    cudaMemcpyToSymbolAsync = 47
    cudaMemcpyFromSymbolAsync = 48
    cudaMemset = 49
    cudaMemset2D = 50
    cudaMemsetAsync = 51
    cudaMemset2DAsync = 52
    cudaGetSymbolAddress = 53
    cudaGetSymbolSize = 54
    cudaBindTexture = 55
    cudaBindTexture2D = 56
    cudaBindTextureToArray = 57
    cudaUnbindTexture = 58
    cudaGetTextureAlignmentOffset = 59
    cudaGetTextureReference = 60
    cudaBindSurfaceToArray = 61
    cudaGetSurfaceReference = 62
    cudaGLSetGLDevice = 63
    cudaGLRegisterBufferObject = 64
    cudaGLMapBufferObject = 65
    cudaGLUnmapBufferObject = 66
    cudaGLUnregisterBufferObject = 67
    cudaGLSetBufferObjectMapFlags = 68
    cudaGLMapBufferObjectAsync = 69
    cudaGLUnmapBufferObjectAsync = 70
    cudaWGLGetDevice = 71
    cudaGraphicsGLRegisterImage = 72
    cudaGraphicsGLRegisterBuffer = 73
    cudaGraphicsUnregisterResource = 74
    cudaGraphicsResourceSetMapFlags = 75
    cudaGraphicsMapResources = 76
    cudaGraphicsUnmapResources = 77
    cudaGraphicsResourceGetMappedPointer = 78
    cudaGraphicsSubResourceGetMappedArray = 79
    cudaVDPAUGetDevice = 80
    cudaVDPAUSetVDPAUDevice = 81
    cudaGraphicsVDPAURegisterVideoSurface = 82
    cudaGraphicsVDPAURegisterOutputSurface = 83
    cudaD3D11GetDevice = 84
    cudaD3D11GetDevices = 85
    cudaD3D11SetDirect3DDevice = 86
    cudaGraphicsD3D11RegisterResource = 87
    cudaD3D10GetDevice = 88
    cudaD3D10GetDevices = 89
    cudaD3D10SetDirect3DDevice = 90
    cudaGraphicsD3D10RegisterResource = 91
    cudaD3D10RegisterResource = 92
    cudaD3D10UnregisterResource = 93
    cudaD3D10MapResources = 94
    cudaD3D10UnmapResources = 95
    cudaD3D10ResourceSetMapFlags = 96
    cudaD3D10ResourceGetSurfaceDimensions = 97
    cudaD3D10ResourceGetMappedArray = 98
    cudaD3D10ResourceGetMappedPointer = 99
    cudaD3D10ResourceGetMappedSize = 100
    cudaD3D10ResourceGetMappedPitch = 101
    cudaD3D9GetDevice = 102
    cudaD3D9GetDevices = 103
    cudaD3D9SetDirect3DDevice = 104
    cudaD3D9GetDirect3DDevice = 105
    cudaGraphicsD3D9RegisterResource = 106
    cudaD3D9RegisterResource = 107
    cudaD3D9UnregisterResource = 108
    cudaD3D9MapResources = 109
    cudaD3D9UnmapResources = 110
    cudaD3D9ResourceSetMapFlags = 111
    cudaD3D9ResourceGetSurfaceDimensions = 112
    cudaD3D9ResourceGetMappedArray = 113
    cudaD3D9ResourceGetMappedPointer = 114
    cudaD3D9ResourceGetMappedSize = 115
    cudaD3D9ResourceGetMappedPitch = 116
    cudaD3D9Begin = 117
    cudaD3D9End = 118
    cudaD3D9RegisterVertexBuffer = 119
    cudaD3D9UnregisterVertexBuffer = 120
    cudaD3D9MapVertexBuffer = 121
    cudaD3D9UnmapVertexBuffer = 122
    cudaThreadExit = 123
    cudaSetDoubleForDevice = 124
    cudaSetDoubleForHost = 125
    cudaThreadSynchronize = 126
    cudaThreadGetLimit = 127
    cudaThreadSetLimit = 128
    cudaStreamCreate = 129
    cudaStreamDestroy = 130
    cudaStreamSynchronize = 131
    cudaStreamQuery = 132
    cudaEventCreate = 133
    cudaEventCreateWithFlags = 134
    cudaEventRecord = 135
    cudaEventDestroy = 136
    cudaEventSynchronize = 137
    cudaEventQuery = 138
    cudaEventElapsedTime = 139
    cudaMalloc3D = 140
    cudaMalloc3DArray = 141
    cudaMemset3D = 142
    cudaMemset3DAsync = 143
    cudaMemcpy3D = 144
    cudaMemcpy3DAsync = 145
    cudaThreadSetCacheConfig = 146
    cudaStreamWaitEvent = 147
    cudaD3D11GetDirect3DDevice = 148
    cudaD3D10GetDirect3DDevice = 149
    cudaThreadGetCacheConfig = 150
    cudaPointerGetAttributes = 151
    cudaHostRegister = 152
    cudaHostUnregister = 153
    cudaDeviceCanAccessPeer = 154
    cudaDeviceEnablePeerAccess = 155
    cudaDeviceDisablePeerAccess = 156
    cudaPeerRegister = 157
    cudaPeerUnregister = 158
    cudaPeerGetDevicePointer = 159
    cudaMemcpyPeer = 160
    cudaMemcpyPeerAsync = 161
    cudaMemcpy3DPeer = 162
    cudaMemcpy3DPeerAsync = 163
    cudaDeviceReset = 164
    cudaDeviceSynchronize = 165
    cudaDeviceGetLimit = 166
    cudaDeviceSetLimit = 167
    cudaDeviceGetCacheConfig = 168
    cudaDeviceSetCacheConfig = 169
    cudaProfilerInitialize = 170
    cudaProfilerStart = 171
    cudaProfilerStop = 172
    cudaDeviceGetByPCIBusId = 173
    cudaDeviceGetPCIBusId = 174
    cudaGLGetDevices = 175
    cudaIpcGetEventHandle = 176
    cudaIpcOpenEventHandle = 177
    cudaIpcGetMemHandle = 178
    cudaIpcOpenMemHandle = 179
    cudaIpcCloseMemHandle = 180
    cudaArrayGetInfo = 181
    cudaFuncSetSharedMemConfig = 182
    cudaDeviceGetSharedMemConfig = 183
    cudaDeviceSetSharedMemConfig = 184
    cudaCreateTextureObject = 185
    cudaDestroyTextureObject = 186
    cudaGetTextureObjectResourceDesc = 187
    cudaGetTextureObjectTextureDesc = 188
    cudaCreateSurfaceObject = 189
    cudaDestroySurfaceObject = 190
    cudaGetSurfaceObjectResourceDesc = 191
    cudaMallocMipmappedArray = 192
    cudaGetMipmappedArrayLevel = 193
    cudaFreeMipmappedArray = 194
    cudaBindTextureToMipmappedArray = 195
    cudaGraphicsResourceGetMappedMipmappedArray = 196
    cudaStreamAddCallback = 197
    cudaStreamCreateWithFlags = 198
    cudaGetTextureObjectResourceViewDesc = 199
    cudaDeviceGetAttribute = 200
    cudaStreamDestroy_v5050 = 201
    cudaStreamCreateWithPriority = 202
    cudaStreamGetPriority = 203
    cudaStreamGetFlags = 204
    cudaDeviceGetStreamPriorityRange = 205
    cudaMallocManaged = 206
    cudaOccupancyMaxActiveBlocksPerMultiprocessor_v6000 = 207
    cudaStreamAttachMemAsync = 208
    cudaGetErrorName = 209
    cudaOccupancyMaxActiveBlocksPerMultiprocessor_v6050 = 210
    cudaLaunchKernel = 211
    cudaGetDeviceFlags = 212
    cudaLaunch_ptsz = 213
    cudaLaunchKernel_ptsz = 214
    cudaMemcpy_ptds = 215
    cudaMemcpy2D_ptds = 216
    cudaMemcpyToArray_ptds = 217
    cudaMemcpy2DToArray_ptds = 218
    cudaMemcpyFromArray_ptds = 219
    cudaMemcpy2DFromArray_ptds = 220
    cudaMemcpyArrayToArray_ptds = 221
    cudaMemcpy2DArrayToArray_ptds = 222
    cudaMemcpyToSymbol_ptds = 223
    cudaMemcpyFromSymbol_ptds = 224
    cudaMemcpyAsync_ptsz = 225
    cudaMemcpyToArrayAsync_ptsz = 226
    cudaMemcpyFromArrayAsync_ptsz = 227
    cudaMemcpy2DAsync_ptsz = 228
    cudaMemcpy2DToArrayAsync_ptsz = 229
    cudaMemcpy2DFromArrayAsync_ptsz = 230
    cudaMemcpyToSymbolAsync_ptsz = 231
    cudaMemcpyFromSymbolAsync_ptsz = 232
    cudaMemset_ptds = 233
    cudaMemset2D_ptds = 234
    cudaMemsetAsync_ptsz = 235
    cudaMemset2DAsync_ptsz = 236
    cudaStreamGetPriority_ptsz = 237
    cudaStreamGetFlags_ptsz = 238
    cudaStreamSynchronize_ptsz = 239
    cudaStreamQuery_ptsz = 240
    cudaStreamAttachMemAsync_ptsz = 241
    cudaEventRecord_ptsz = 242
    cudaMemset3D_ptds = 243
    cudaMemset3DAsync_ptsz = 244
    cudaMemcpy3D_ptds = 245
    cudaMemcpy3DAsync_ptsz = 246
    cudaStreamWaitEvent_ptsz = 247
    cudaStreamAddCallback_ptsz = 248
    cudaMemcpy3DPeer_ptds = 249
    cudaMemcpy3DPeerAsync_ptsz = 250
    cudaOccupancyMaxActiveBlocksPerMultiprocessorWithFlags = 251
    cudaMemPrefetchAsync = 252
    cudaMemPrefetchAsync_ptsz = 253
    cudaMemAdvise = 254
    cudaDeviceGetP2PAttribute = 255
    cudaGraphicsEGLRegisterImage = 256
    cudaEGLStreamConsumerConnect = 257
    cudaEGLStreamConsumerDisconnect = 258
    cudaEGLStreamConsumerAcquireFrame = 259
    cudaEGLStreamConsumerReleaseFrame = 260
    cudaEGLStreamProducerConnect = 261
    cudaEGLStreamProducerDisconnect = 262
    cudaEGLStreamProducerPresentFrame = 263
    cudaEGLStreamProducerReturnFrame = 264
    cudaGraphicsResourceGetMappedEglFrame = 265
    cudaMemRangeGetAttribute = 266
    cudaMemRangeGetAttributes = 267
    cudaEGLStreamConsumerConnectWithFlags = 268
    cudaLaunchCooperativeKernel = 269
    cudaLaunchCooperativeKernel_ptsz = 270
    cudaEventCreateFromEGLSync = 271
    cudaLaunchCooperativeKernelMultiDevice = 272
    cudaFuncSetAttribute = 273
    cudaImportExternalMemory = 274
    cudaExternalMemoryGetMappedBuffer = 275
    cudaExternalMemoryGetMappedMipmappedArray = 276
    cudaDestroyExternalMemory = 277
    cudaImportExternalSemaphore = 278
    cudaSignalExternalSemaphoresAsync = 279
    cudaSignalExternalSemaphoresAsync_ptsz = 280
    cudaWaitExternalSemaphoresAsync = 281
    cudaWaitExternalSemaphoresAsync_ptsz = 282
    cudaDestroyExternalSemaphore = 283
    cudaLaunchHostFunc = 284
    cudaLaunchHostFunc_ptsz = 285
    cudaGraphCreate = 286
    cudaGraphKernelNodeGetParams = 287
    cudaGraphKernelNodeSetParams = 288
    cudaGraphAddKernelNode = 289
    cudaGraphAddMemcpyNode = 290
    cudaGraphMemcpyNodeGetParams = 291
    cudaGraphMemcpyNodeSetParams = 292
    cudaGraphAddMemsetNode = 293
    cudaGraphMemsetNodeGetParams = 294
    cudaGraphMemsetNodeSetParams = 295
    cudaGraphAddHostNode = 296
    cudaGraphHostNodeGetParams = 297
    cudaGraphAddChildGraphNode = 298
    cudaGraphChildGraphNodeGetGraph = 299
    cudaGraphAddEmptyNode = 300
    cudaGraphClone = 301
    cudaGraphNodeFindInClone = 302
    cudaGraphNodeGetType = 303
    cudaGraphGetRootNodes = 304
    cudaGraphNodeGetDependencies = 305
    cudaGraphNodeGetDependentNodes = 306
    cudaGraphAddDependencies = 307
    cudaGraphRemoveDependencies = 308
    cudaGraphDestroyNode = 309
    cudaGraphInstantiate = 310
    cudaGraphLaunch = 311
    cudaGraphLaunch_ptsz = 312
    cudaGraphExecDestroy = 313
    cudaGraphDestroy = 314
    cudaStreamBeginCapture = 315
    cudaStreamBeginCapture_ptsz = 316
    cudaStreamIsCapturing = 317
    cudaStreamIsCapturing_ptsz = 318
    cudaStreamEndCapture = 319
    cudaStreamEndCapture_ptsz = 320
    cudaGraphHostNodeSetParams = 321
    cudaGraphGetNodes = 322
    cudaGraphGetEdges = 323
    cudaStreamGetCaptureInfo = 324
    cudaStreamGetCaptureInfo_ptsz = 325
    cudaGraphExecKernelNodeSetParams = 326
    cudaThreadExchangeStreamCaptureMode = 327
    cudaDeviceGetNvSciSyncAttributes = 328
    cudaOccupancyAvailableDynamicSMemPerBlock = 329
    cudaStreamSetFlags = 330
    cudaStreamSetFlags_ptsz = 331
    cudaGraphExecMemcpyNodeSetParams = 332
    cudaGraphExecMemsetNodeSetParams = 333
    cudaGraphExecHostNodeSetParams = 334
    cudaGraphExecUpdate = 335
    SIZE = 336
    FORCE_INT = 0x7FFFFFFF


def sizeof_fmt(num, suffix='B'):
    """Format size with metric units (like nvvp)"""
    for unit in ['','K','M','G','T','P','E','Z']:
        if abs(num) < 1000.0:
            return "%3.1f%s%s" % (num, unit, suffix)
        num /= 1000.0
    return "%.1f%s%s" % (num, 'Y', suffix)

def eprintRow(row):
    """Print a sqlite3.Row to stderr."""
    for k in row.keys():
        eprint("{}: {}".format(k, row[k]))
    eprint("----")

def eprint(*args, **kwargs):
    """Print to stderr."""
    print(*args, file=sys.stderr, **kwargs)

def inspect_db(conn):
    """Quickly dump data of all tables in database, with field names."""
    tables = """
CUPTI_ACTIVITY_KIND_BRANCH
CUPTI_ACTIVITY_KIND_CDP_KERNEL
CUPTI_ACTIVITY_KIND_CONCURRENT_KERNEL
CUPTI_ACTIVITY_KIND_CONTEXT
CUPTI_ACTIVITY_KIND_CUDA_EVENT
CUPTI_ACTIVITY_KIND_DEVICE
CUPTI_ACTIVITY_KIND_DEVICE_ATTRIBUTE
CUPTI_ACTIVITY_KIND_DRIVER
CUPTI_ACTIVITY_KIND_ENVIRONMENT
CUPTI_ACTIVITY_KIND_EVENT
CUPTI_ACTIVITY_KIND_EVENT_INSTANCE
CUPTI_ACTIVITY_KIND_EXTERNAL_CORRELATION
CUPTI_ACTIVITY_KIND_FUNCTION
CUPTI_ACTIVITY_KIND_GLOBAL_ACCESS
CUPTI_ACTIVITY_KIND_INSTANTANEOUS_EVENT
CUPTI_ACTIVITY_KIND_INSTANTANEOUS_EVENT_INSTANCE
CUPTI_ACTIVITY_KIND_INSTANTANEOUS_METRIC
CUPTI_ACTIVITY_KIND_INSTANTANEOUS_METRIC_INSTANCE
CUPTI_ACTIVITY_KIND_INSTRUCTION_CORRELATION
CUPTI_ACTIVITY_KIND_INSTRUCTION_EXECUTION
CUPTI_ACTIVITY_KIND_KERNEL
CUPTI_ACTIVITY_KIND_MARKER
CUPTI_ACTIVITY_KIND_MARKER_DATA
CUPTI_ACTIVITY_KIND_MEMCPY
CUPTI_ACTIVITY_KIND_MEMCPY2
CUPTI_ACTIVITY_KIND_MEMSET
CUPTI_ACTIVITY_KIND_METRIC
CUPTI_ACTIVITY_KIND_METRIC_INSTANCE
CUPTI_ACTIVITY_KIND_MODULE
CUPTI_ACTIVITY_KIND_NAME
CUPTI_ACTIVITY_KIND_NVLINK
CUPTI_ACTIVITY_KIND_OPENACC_DATA
CUPTI_ACTIVITY_KIND_OPENACC_LAUNCH
CUPTI_ACTIVITY_KIND_OPENACC_OTHER
CUPTI_ACTIVITY_KIND_OVERHEAD
CUPTI_ACTIVITY_KIND_PC_SAMPLING
CUPTI_ACTIVITY_KIND_PC_SAMPLING_RECORD_INFO
CUPTI_ACTIVITY_KIND_PREEMPTION
CUPTI_ACTIVITY_KIND_RUNTIME
CUPTI_ACTIVITY_KIND_SHARED_ACCESS
CUPTI_ACTIVITY_KIND_SOURCE_LOCATOR
CUPTI_ACTIVITY_KIND_STREAM
CUPTI_ACTIVITY_KIND_SYNCHRONIZATION
CUPTI_ACTIVITY_KIND_UNIFIED_MEMORY_COUNTER
""".strip().split("\n")

    for t in tables:
        eprint(t)
        for r in conn.execute("SELECT * FROM {} LIMIT 4".format(t)):
            eprintRow(r)
        eprint("----")
        eprint("----")
        eprint("----")
        eprint("----")

if __name__ == "__main__":
    main()
