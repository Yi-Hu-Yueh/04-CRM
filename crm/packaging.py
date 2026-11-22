"""Allowlisted application-only downloads. Never traverse the working directory."""
import hashlib
import json
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path
from .release import APP_VERSION

class PackageError(Exception):
    pass

@dataclass(frozen=True)
class Release:
    executable: Path
    sha256: str
    size: int

def digest(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()

def inspect_release(executable, metadata=None):
    executable=Path(executable)
    try:
        with executable.open('rb') as stream:
            if stream.read(2)!=b'MZ':raise PackageError('獨立系統檔案無效，請重新封裝後再試。')
        actual=digest(executable)
        if metadata is not None:
            manifest=json.loads(Path(metadata).read_text(encoding='utf-8'))
            if manifest.get('version')!=APP_VERSION or manifest.get('sha256')!=actual:
                raise PackageError('獨立系統版本或完整性檢查未通過，請重新封裝後再試。')
        return Release(executable,actual,executable.stat().st_size)
    except (OSError,ValueError) as exc:
        raise PackageError('獨立系統尚未備妥，請稍後再試。') from exc

def release_metadata(release):
    return {'product':'04-CRM','version':APP_VERSION,'platform':'windows-x64',
            'executable':'CRM.exe','sha256':release.sha256,'bytes':release.size,
            'contains_business_data':False,'contains_accounts':False}

def create_archive(release, assets, temporary_directory):
    """Four explicit entries, including the executable only; no user-selected paths."""
    assets=Path(assets)
    temporary_directory=Path(temporary_directory)
    temporary_directory.mkdir(parents=True,exist_ok=True)
    result=tempfile.SpooledTemporaryFile(max_size=8*1024*1024,dir=temporary_directory,mode='w+b')
    try:
        with zipfile.ZipFile(result,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
            # Hash the exact bytes written, not an earlier path snapshot.
            hasher=hashlib.sha256()
            with release.executable.open('rb') as source,archive.open('CRM/CRM.exe','w',force_zip64=True) as target:
                while chunk:=source.read(1024*1024):
                    target.write(chunk);hasher.update(chunk)
            if hasher.hexdigest()!=release.sha256:raise PackageError('獨立系統檔案已變更，請重新匯出。')
            archive.writestr('CRM/release.json',json.dumps(release_metadata(release),ensure_ascii=False,indent=2))
            archive.writestr('CRM/使用說明.txt',(assets/'使用說明.txt').read_bytes())
            archive.writestr('CRM/THIRD-PARTY-NOTICES.txt',(assets/'THIRD-PARTY-NOTICES.txt').read_bytes())
        result.seek(0)
        return result
    except Exception:
        result.close()
        raise
