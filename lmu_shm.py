"""Czytnik pamieci wspoldzielonej Le Mans Ultimate (LMU_Data).

Struktury 1:1 z naglowka Studio 397:
    <LMU>/Support/SharedMemoryInterface/InternalsPlugin.hpp
    <LMU>/Support/SharedMemoryInterface/SharedMemoryInterface.hpp
Naglowek jest kompilowany z #pragma pack(push, 4), dlatego wszystkie
struktury maja _pack_ = 4.

Kluczowa wlasciwosc: SharedMemoryTelemetryData zawiera tablice
TelemInfoV01[104] dla WSZYSTKICH aktywnych pojazdow (activeVehicles),
z throttle/brake/steering, pozycja, okrazeniem i czasem sesji.
"""

from __future__ import annotations

import ctypes
import mmap
import os
import struct
import sys
from ctypes import wintypes

MAX_VEHICLES = 104
MAX_PATH = 260

LMU_SHARED_MEMORY_FILE = "LMU_Data"
LMU_HOLD_EVENT = "LMU_Data_HoldEvent"
LMU_DATA_EVENT = "LMU_Data_DataEvent"
LMU_LOCK_DATA = "LMU_SharedMemoryLockData"
LMU_LOCK_EVENT = "LMU_SharedMemoryLockEvent"


# ---------------------------------------------------------------- podstawy
class TelemVect3(ctypes.Structure):
    _pack_ = 4
    _fields_ = [("x", ctypes.c_double),
                ("y", ctypes.c_double),
                ("z", ctypes.c_double)]


class TelemWheelV01(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("mSuspensionDeflection", ctypes.c_double),
        ("mRideHeight", ctypes.c_double),
        ("mSuspForce", ctypes.c_double),
        ("mBrakeTemp", ctypes.c_double),
        ("mBrakePressure", ctypes.c_double),
        ("mRotation", ctypes.c_double),
        ("mLateralPatchVel", ctypes.c_double),
        ("mLongitudinalPatchVel", ctypes.c_double),
        ("mLateralGroundVel", ctypes.c_double),
        ("mLongitudinalGroundVel", ctypes.c_double),
        ("mCamber", ctypes.c_double),
        ("mLateralForce", ctypes.c_double),
        ("mLongitudinalForce", ctypes.c_double),
        ("mTireLoad", ctypes.c_double),
        ("mGripFract", ctypes.c_double),
        ("mPressure", ctypes.c_double),
        ("mTemperature", ctypes.c_double * 3),
        ("mWear", ctypes.c_double),
        ("mTerrainName", ctypes.c_char * 16),
        ("mSurfaceType", ctypes.c_ubyte),
        ("mFlat", ctypes.c_bool),
        ("mDetached", ctypes.c_bool),
        ("mStaticUndeflectedRadius", ctypes.c_ubyte),
        ("mVerticalTireDeflection", ctypes.c_double),
        ("mWheelYLocation", ctypes.c_double),
        ("mToe", ctypes.c_double),
        ("mTireCarcassTemperature", ctypes.c_double),
        ("mTireInnerLayerTemperature", ctypes.c_double * 3),
        ("mOptimalTemp", ctypes.c_float),
        ("mCompoundIndex", ctypes.c_ubyte),
        ("mCompoundType", ctypes.c_ubyte),
        ("mExpansion", ctypes.c_ubyte * 18),
    ]


class TelemInfoV01(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("mID", ctypes.c_int32),
        ("mDeltaTime", ctypes.c_double),
        ("mElapsedTime", ctypes.c_double),
        ("mLapNumber", ctypes.c_int32),
        ("mLapStartET", ctypes.c_double),
        ("mVehicleName", ctypes.c_char * 64),
        ("mTrackName", ctypes.c_char * 64),
        ("mPos", TelemVect3),
        ("mLocalVel", TelemVect3),
        ("mLocalAccel", TelemVect3),
        ("mOri", TelemVect3 * 3),
        ("mLocalRot", TelemVect3),
        ("mLocalRotAccel", TelemVect3),
        ("mGear", ctypes.c_int32),
        ("mEngineRPM", ctypes.c_double),
        ("mEngineWaterTemp", ctypes.c_double),
        ("mEngineOilTemp", ctypes.c_double),
        ("mClutchRPM", ctypes.c_double),
        ("mUnfilteredThrottle", ctypes.c_double),
        ("mUnfilteredBrake", ctypes.c_double),
        ("mUnfilteredSteering", ctypes.c_double),
        ("mUnfilteredClutch", ctypes.c_double),
        ("mFilteredThrottle", ctypes.c_double),
        ("mFilteredBrake", ctypes.c_double),
        ("mFilteredSteering", ctypes.c_double),
        ("mFilteredClutch", ctypes.c_double),
        ("mSteeringShaftTorque", ctypes.c_double),
        ("mFront3rdDeflection", ctypes.c_double),
        ("mRear3rdDeflection", ctypes.c_double),
        ("mFrontWingHeight", ctypes.c_double),
        ("mFrontRideHeight", ctypes.c_double),
        ("mRearRideHeight", ctypes.c_double),
        ("mDrag", ctypes.c_double),
        ("mFrontDownforce", ctypes.c_double),
        ("mRearDownforce", ctypes.c_double),
        ("mFuel", ctypes.c_double),
        ("mEngineMaxRPM", ctypes.c_double),
        ("mScheduledStops", ctypes.c_ubyte),
        ("mOverheating", ctypes.c_bool),
        ("mDetached", ctypes.c_bool),
        ("mHeadlights", ctypes.c_bool),
        ("mDentSeverity", ctypes.c_ubyte * 8),
        ("mLastImpactET", ctypes.c_double),
        ("mLastImpactMagnitude", ctypes.c_double),
        ("mLastImpactPos", TelemVect3),
        ("mEngineTorque", ctypes.c_double),
        ("mCurrentSector", ctypes.c_int32),
        ("mSpeedLimiter", ctypes.c_ubyte),
        ("mMaxGears", ctypes.c_ubyte),
        ("mFrontTireCompoundIndex", ctypes.c_ubyte),
        ("mRearTireCompoundIndex", ctypes.c_ubyte),
        ("mFuelCapacity", ctypes.c_double),
        ("mFrontFlapActivated", ctypes.c_ubyte),
        ("mRearFlapActivated", ctypes.c_ubyte),
        ("mRearFlapLegalStatus", ctypes.c_ubyte),
        ("mIgnitionStarter", ctypes.c_ubyte),
        ("mFrontTireCompoundName", ctypes.c_char * 18),
        ("mRearTireCompoundName", ctypes.c_char * 18),
        ("mSpeedLimiterAvailable", ctypes.c_ubyte),
        ("mAntiStallActivated", ctypes.c_ubyte),
        ("mUnused", ctypes.c_ubyte * 2),
        ("mVisualSteeringWheelRange", ctypes.c_float),
        ("mRearBrakeBias", ctypes.c_double),
        ("mTurboBoostPressure", ctypes.c_double),
        ("mPhysicsToGraphicsOffset", ctypes.c_float * 3),
        ("mPhysicalSteeringWheelRange", ctypes.c_float),
        ("mDeltaBest", ctypes.c_double),
        ("mBatteryChargeFraction", ctypes.c_double),
        ("mElectricBoostMotorTorque", ctypes.c_double),
        ("mElectricBoostMotorRPM", ctypes.c_double),
        ("mElectricBoostMotorTemperature", ctypes.c_double),
        ("mElectricBoostWaterTemperature", ctypes.c_double),
        ("mElectricBoostMotorState", ctypes.c_ubyte),
        ("mLapInvalidated", ctypes.c_bool),
        ("mABSActive", ctypes.c_bool),
        ("mTCActive", ctypes.c_bool),
        ("mSpeedLimiterActive", ctypes.c_bool),
        ("mWiperState", ctypes.c_ubyte),
        ("mTC", ctypes.c_ubyte),
        ("mTCMax", ctypes.c_ubyte),
        ("mTCSlip", ctypes.c_ubyte),
        ("mTCSlipMax", ctypes.c_ubyte),
        ("mTCCut", ctypes.c_ubyte),
        ("mTCCutMax", ctypes.c_ubyte),
        ("mABS", ctypes.c_ubyte),
        ("mABSMax", ctypes.c_ubyte),
        ("mMotorMap", ctypes.c_ubyte),
        ("mMotorMapMax", ctypes.c_ubyte),
        ("mMigration", ctypes.c_ubyte),
        ("mMigrationMax", ctypes.c_ubyte),
        ("mFrontAntiSway", ctypes.c_ubyte),
        ("mFrontAntiSwayMax", ctypes.c_ubyte),
        ("mRearAntiSway", ctypes.c_ubyte),
        ("mRearAntiSwayMax", ctypes.c_ubyte),
        ("mLiftAndCoastProgress", ctypes.c_ubyte),
        ("mTrackLimitsSteps", ctypes.c_ubyte),
        ("mRegen", ctypes.c_float),
        ("mSoC", ctypes.c_float),
        ("mVirtualEnergy", ctypes.c_float),
        ("mTimeGapCarAhead", ctypes.c_float),
        ("mTimeGapCarBehind", ctypes.c_float),
        ("mTimeGapPlaceAhead", ctypes.c_float),
        ("mTimeGapPlaceBehind", ctypes.c_float),
        ("mVehicleModel", ctypes.c_char * 30),
        ("mVehicleClass", ctypes.c_uint8),        # enum class : uint8_t
        ("mVehicleChampionship", ctypes.c_uint8),  # enum class : uint8_t
        ("mExpansion", ctypes.c_ubyte * 20),
        ("mWheel", TelemWheelV01 * 4),
    ]


# ---------------------------------------------------------------- scoring
class VehicleScoringInfoV01(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("mID", ctypes.c_int32),
        ("mDriverName", ctypes.c_char * 32),
        ("mVehicleName", ctypes.c_char * 64),
        ("mTotalLaps", ctypes.c_short),
        ("mSector", ctypes.c_byte),
        ("mFinishStatus", ctypes.c_byte),
        ("mLapDist", ctypes.c_double),
        ("mPathLateral", ctypes.c_double),
        ("mTrackEdge", ctypes.c_double),
        ("mBestSector1", ctypes.c_double),
        ("mBestSector2", ctypes.c_double),
        ("mBestLapTime", ctypes.c_double),
        ("mLastSector1", ctypes.c_double),
        ("mLastSector2", ctypes.c_double),
        ("mLastLapTime", ctypes.c_double),
        ("mCurSector1", ctypes.c_double),
        ("mCurSector2", ctypes.c_double),
        ("mNumPitstops", ctypes.c_short),
        ("mNumPenalties", ctypes.c_short),
        ("mIsPlayer", ctypes.c_bool),
        ("mControl", ctypes.c_byte),
        ("mInPits", ctypes.c_bool),
        ("mPlace", ctypes.c_ubyte),
        ("mVehicleClass", ctypes.c_char * 32),
        ("mTimeBehindNext", ctypes.c_double),
        ("mLapsBehindNext", ctypes.c_int32),
        ("mTimeBehindLeader", ctypes.c_double),
        ("mLapsBehindLeader", ctypes.c_int32),
        ("mLapStartET", ctypes.c_double),
        ("mPos", TelemVect3),
        ("mLocalVel", TelemVect3),
        ("mLocalAccel", TelemVect3),
        ("mOri", TelemVect3 * 3),
        ("mLocalRot", TelemVect3),
        ("mLocalRotAccel", TelemVect3),
        ("mHeadlights", ctypes.c_ubyte),
        ("mPitState", ctypes.c_ubyte),
        ("mServerScored", ctypes.c_ubyte),
        ("mIndividualPhase", ctypes.c_ubyte),
        ("mQualification", ctypes.c_int32),
        ("mTimeIntoLap", ctypes.c_double),
        ("mEstimatedLapTime", ctypes.c_double),
        ("mPitGroup", ctypes.c_char * 24),
        ("mFlag", ctypes.c_ubyte),
        ("mUnderYellow", ctypes.c_bool),
        ("mCountLapFlag", ctypes.c_ubyte),
        ("mInGarageStall", ctypes.c_bool),
        ("mUpgradePack", ctypes.c_ubyte * 16),
        ("mPitLapDist", ctypes.c_float),
        ("mBestLapSector1", ctypes.c_float),
        ("mBestLapSector2", ctypes.c_float),
        ("mSteamID", ctypes.c_ulonglong),
        ("mVehFilename", ctypes.c_char * 32),
        ("mAttackMode", ctypes.c_short),
        ("mFuelFraction", ctypes.c_ubyte),
        ("mDRSState", ctypes.c_bool),
        ("mExpansion", ctypes.c_ubyte * 4),
    ]


class ScoringInfoV01(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("mTrackName", ctypes.c_char * 64),
        ("mSession", ctypes.c_int32),
        ("mCurrentET", ctypes.c_double),
        ("mEndET", ctypes.c_double),
        ("mMaxLaps", ctypes.c_int32),
        ("mLapDist", ctypes.c_double),
        ("mResultsStream", ctypes.c_void_p),
        ("mNumVehicles", ctypes.c_int32),
        ("mGamePhase", ctypes.c_ubyte),
        ("mYellowFlagState", ctypes.c_byte),
        ("mSectorFlag", ctypes.c_ubyte * 3),
        ("mStartLight", ctypes.c_ubyte),
        ("mNumRedLights", ctypes.c_ubyte),
        ("mInRealtime", ctypes.c_bool),
        ("mPlayerName", ctypes.c_char * 32),
        ("mPlrFileName", ctypes.c_char * 64),
        ("mDarkCloud", ctypes.c_double),
        ("mRaining", ctypes.c_double),
        ("mAmbientTemp", ctypes.c_double),
        ("mTrackTemp", ctypes.c_double),
        ("mWind", TelemVect3),
        ("mMinPathWetness", ctypes.c_double),
        ("mMaxPathWetness", ctypes.c_double),
        ("mGameMode", ctypes.c_ubyte),
        ("mIsPasswordProtected", ctypes.c_bool),
        ("mServerPort", ctypes.c_ushort),
        ("mServerPublicIP", ctypes.c_uint32),
        ("mMaxPlayers", ctypes.c_int32),
        ("mServerName", ctypes.c_char * 32),
        ("mStartET", ctypes.c_float),
        ("mAvgPathWetness", ctypes.c_double),
        ("mSessionTimeRemaining", ctypes.c_float),
        ("mTimeOfDay", ctypes.c_float),
        ("mIsFixedSetup", ctypes.c_bool),
        ("mTrackGripLevel", ctypes.c_uint8),
        ("mCloudCoverage", ctypes.c_uint8),
        ("mTrackLimitsStepsPerPenalty", ctypes.c_uint8),
        ("mTrackLimitsStepsPerPoint", ctypes.c_uint8),
        ("mExpansion", ctypes.c_ubyte * 187),
        ("mVehicle", ctypes.c_void_p),
        # Wyrownanie zaobserwowane w LMU 1.4200: ScoringInfoV01 ma 552 B
        # (o 4 B wiecej niz samo pack(4)), dzieki czemu vehScoringInfo
        # startuje pod offsetem 560 wzgledem SharedMemoryScoringData.
        ("_pad_array_align", ctypes.c_ubyte * 4),
    ]


class ApplicationStateV01(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("mAppWindow", ctypes.c_void_p),
        ("mWidth", ctypes.c_uint32),
        ("mHeight", ctypes.c_uint32),
        ("mRefreshRate", ctypes.c_uint32),
        ("mWindowed", ctypes.c_uint32),
        ("mOptionsLocation", ctypes.c_ubyte),
        ("mOptionsPage", ctypes.c_char * 31),
        ("mExpansion", ctypes.c_ubyte * 204),
    ]


class SharedMemoryScoringData(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("scoringInfo", ScoringInfoV01),
        ("scoringStreamSize", ctypes.c_size_t),
        ("vehScoringInfo", VehicleScoringInfoV01 * MAX_VEHICLES),
        ("scoringStream", ctypes.c_char * 65536),
    ]


class SharedMemoryTelemetryData(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("activeVehicles", ctypes.c_uint8),
        ("playerVehicleIdx", ctypes.c_uint8),
        ("playerHasVehicle", ctypes.c_bool),
        # 1 bajt wyrownania: telemInfo startuje pod offsetem 4.
        ("_pad_array_align", ctypes.c_ubyte * 1),
        ("telemInfo", TelemInfoV01 * MAX_VEHICLES),
    ]


class SharedMemoryPathData(ctypes.Structure):
    _pack_ = 4
    _fields_ = [("userData", ctypes.c_char * MAX_PATH),
                ("customVariables", ctypes.c_char * MAX_PATH),
                ("stewardResults", ctypes.c_char * MAX_PATH),
                ("playerProfile", ctypes.c_char * MAX_PATH),
                ("pluginsFolder", ctypes.c_char * MAX_PATH)]


SME_MAX = 16


class SharedMemoryGeneric(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("events", ctypes.c_uint32 * SME_MAX),
        ("gameVersion", ctypes.c_int32),
        ("FFBTorque", ctypes.c_float),
        ("appInfo", ApplicationStateV01),
    ]


class SharedMemoryObjectOut(ctypes.Structure):
    _pack_ = 4
    _fields_ = [
        ("generic", SharedMemoryGeneric),
        ("paths", SharedMemoryPathData),
        ("scoring", SharedMemoryScoringData),
        ("telemetry", SharedMemoryTelemetryData),
    ]


class SharedMemoryLayout(ctypes.Structure):
    _pack_ = 4
    _fields_ = [("data", SharedMemoryObjectOut)]


SIZE = ctypes.sizeof(SharedMemoryObjectOut)


# ---------------------------------------------------------------- offline
def parse_dump(path: str) -> SharedMemoryObjectOut:
    """Parsuje zrzut LMU_Data (np. plik shm_track.bin) do struktur."""
    raw = open(path, "rb").read()
    if len(raw) < SIZE:
        raise ValueError(f"{path}: {len(raw)} B < sizeof(SharedMemoryObjectOut)={SIZE} B")
    return SharedMemoryObjectOut.from_buffer_copy(raw[:SIZE])


def _txt(b: bytes) -> str:
    return b.split(b"\0", 1)[0].decode("utf-8", "replace")


def vehicles(sm: SharedMemoryObjectOut):
    """Zwraca liste pojazdow: dane telemetrii + scoring scalone po mID."""
    scoring = {}
    n = min(max(sm.scoring.scoringInfo.mNumVehicles, 0), MAX_VEHICLES)
    for i in range(n):
        v = sm.scoring.vehScoringInfo[i]
        scoring[int(v.mID)] = v

    out = []
    for i in range(min(int(sm.telemetry.activeVehicles), MAX_VEHICLES)):
        t = sm.telemetry.telemInfo[i]
        s = scoring.get(int(t.mID))
        out.append({
            "slot": i,
            "id": int(t.mID),
            "driver": _txt(s.mDriverName) if s else "",
            "vehicle": _txt(t.mVehicleName),
            "model": _txt(t.mVehicleModel),
            "class": _txt(s.mVehicleClass) if s else "",
            "is_player": bool(s.mIsPlayer) if s else False,
            "control": int(s.mControl) if s else None,
            "place": int(s.mPlace) if s else None,
            "lap": int(t.mLapNumber),
            "lap_start_et": float(t.mLapStartET),
            "elapsed": float(t.mElapsedTime),
            "lap_dist": float(s.mLapDist) if s else None,
            "path_lateral": float(s.mPathLateral) if s else None,
            "track_edge": float(s.mTrackEdge) if s else None,
            "best_lap": float(s.mBestLapTime) if s else None,
            "last_lap": float(s.mLastLapTime) if s else None,
            "throttle": float(t.mUnfilteredThrottle),
            "brake": float(t.mUnfilteredBrake),
            "steering": float(t.mUnfilteredSteering),
            "gear": int(t.mGear),
            "rpm": float(t.mEngineRPM),
            "speed": float(t.mLocalVel.z),  # +z = do tylu; ujemne = do przodu
            "pos": (float(t.mPos.x), float(t.mPos.y), float(t.mPos.z)),
            "fuel": float(t.mFuel),
            "in_pits": bool(s.mInPits) if s else False,
            "sector": int(t.mCurrentSector),
        })
    return out


# ---------------------------------------------------------------- live
class SharedMemoryError(RuntimeError):
    pass


class LmuSharedMemory:
    """Podlaczenie do zywego LMU_Data z uzyciem locka i eventow LMU.

    Uzycie:
        with LmuSharedMemory() as shm:
            shm.wait_update(500)          # ms; False = timeout
            snap = shm.snapshot()         # SharedMemoryObjectOut (kopia)
    """

    def __init__(self, timeout_ms: int = 200):
        self.timeout_ms = timeout_ms
        self._mm = None
        self._lock_mm = None
        self._hold = None
        self._data_ev = None
        self._lock_ev = None
        self.has_events = False
        self._k32 = ctypes.WinDLL("kernel32", use_last_error=True)
        self._k32.OpenEventA.restype = wintypes.HANDLE
        self._k32.OpenEventA.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.LPCSTR]
        self._k32.CloseHandle.argtypes = [wintypes.HANDLE]
        self._k32.CloseHandle.restype = wintypes.BOOL
        self._k32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
        self._k32.WaitForSingleObject.restype = wintypes.DWORD
        self._k32.SetEvent.argtypes = [wintypes.HANDLE]
        self._k32.SetEvent.restype = wintypes.BOOL

    # -- lifecycle ----------------------------------------------------
    def open(self) -> "LmuSharedMemory":
        try:
            self._mm = mmap.mmap(-1, SIZE, tagname=LMU_SHARED_MEMORY_FILE)
        except (OSError, ValueError) as e:
            raise SharedMemoryError(
                f"Cannot open '{LMU_SHARED_MEMORY_FILE}' ({e}). "
                "Is LMU running with plugins enabled?") from e

        # SYNCHRONIZE = 0x00100000
        self._hold = self._k32.OpenEventA(0x00100000, False, LMU_HOLD_EVENT.encode())
        self._data_ev = self._k32.OpenEventA(0x00100000, False, LMU_DATA_EVENT.encode())
        if not self._hold or not self._data_ev:
            # Starsze buildy / atrapy moga nie miec eventow - zostaje polling.
            for h in (self._hold, self._data_ev):
                if h:
                    self._k32.CloseHandle(h)
            self._hold = self._data_ev = None

        # Bez locka: LMU sygnalizuje Hold/Data, a snapshot() dodatkowo
        # sprawdza spojnosc dwoch kolejnych odczytow. Interlocked* to
        # intrinsics i nie sa eksportowane z kernel32 na Win64.
        self._lock_mm = None
        self._lock_ev = None
        self.has_events = bool(self._hold and self._data_ev)
        return self

    def close(self) -> None:
        for h in (self._hold, self._data_ev, self._lock_ev):
            if h:
                self._k32.CloseHandle(h)
        for mm in (self._mm, self._lock_mm):
            if mm:
                try:
                    mm.close()
                except BufferError:
                    pass
        self._mm = self._lock_mm = None
        self._hold = self._data_ev = self._lock_ev = None

    def __enter__(self):
        return self.open()

    def __exit__(self, *exc):
        self.close()

    # -- read ---------------------------------------------------------
    def wait_update(self, timeout_ms: int | None = None) -> bool:
        """Czeka na nowa klatke. False = timeout/brak danych."""
        ms = self.timeout_ms if timeout_ms is None else timeout_ms
        if not self._hold or not self._data_ev:
            return False
        if self._k32.WaitForSingleObject(self._hold, ms) != 0:
            return False
        if self._k32.WaitForSingleObject(self._data_ev, ms) != 0:
            return False
        return True

    def snapshot(self) -> SharedMemoryObjectOut:
        if not self._mm:
            raise SharedMemoryError("LMU_Data is not open")
        raw = self._mm[:SIZE]
        for _ in range(5):
            raw2 = self._mm[:SIZE]
            if raw2 == raw:
                return SharedMemoryObjectOut.from_buffer_copy(raw)
            raw = raw2
        return SharedMemoryObjectOut.from_buffer_copy(raw)


def _main(argv: list[str]) -> int:
    if len(argv) > 1:
        sm = parse_dump(argv[1])
        print(f"sizeof(SharedMemoryObjectOut)={SIZE} B")
        si = sm.scoring.scoringInfo
        print(f"gameVersion={sm.generic.gameVersion} track={_txt(si.mTrackName)!r} "
              f"session={si.mSession} phase={si.mGamePhase} mode={si.mGameMode} "
              f"vehicles={si.mNumVehicles} active={sm.telemetry.activeVehicles} "
              f"player={_txt(si.mPlayerName)!r}")
        for v in vehicles(sm):
            print(f"[{v['slot']:2}] id={v['id']:3} {v['driver'][:20]:20} "
                  f"{v['vehicle'][:28]:28} lap={v['lap']} thr={v['throttle']:.2f} "
                  f"brk={v['brake']:.2f} str={v['steering']:+.2f} gear={v['gear']} "
                  f"best={v['best_lap']:.3f} ctrl={v['control']}")
        return 0

    with LmuSharedMemory() as shm:
        for _ in range(200):
            if shm.wait_update(1000):
                break
        sm = shm.snapshot()
        si = sm.scoring.scoringInfo
        print(f"track={_txt(si.mTrackName)!r} session={si.mSession} "
              f"vehicles={si.mNumVehicles} active={sm.telemetry.activeVehicles}")
        for v in vehicles(sm):
            print(f"[{v['slot']:2}] id={v['id']:3} {v['driver'][:20]:20} "
                  f"{v['vehicle'][:28]:28} lap={v['lap']} thr={v['throttle']:.2f} "
                  f"brk={v['brake']:.2f} pos={v['pos']} dist={v['lap_dist']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv))
