from __future__ import annotations
import os
import json
import datetime
import secrets
from pathlib import Path
from typing import Optional, List, Any
from ..resources import FileResource
from ..exception import FrictionlessException
from ..package import Package
from ..resource import Resource
from .database import Database
from .filesystem import Filesystem
from .interfaces import IQueryData, ITable, IFile, IFileItem, IFieldItem
from ..resources import JsonResource, TextResource
from .. import settings
from .. import helpers
from .. import portals

# TODO: handle method errors?


class Project:
    is_root: bool
    session: Optional[str]
    public: Path
    private: Path
    database: Database
    filesystem: Filesystem

    def __init__(
        self,
        *,
        basepath: Optional[str] = None,
        session: Optional[str] = None,
        is_root: bool = False,
    ):
        # Provide authz
        base = Path(basepath or "")
        if is_root:
            assert not session
        if not is_root:
            if not session:
                session = secrets.token_urlsafe(16)
            assert len(session) == 22

        # Ensure structure
        public = base / (session or "")
        private = public / ".frictionless"
        database = private / "project.db"
        public.mkdir(parents=True, exist_ok=True)
        private.mkdir(parents=True, exist_ok=True)

        # Store attributes
        self.is_root = is_root
        self.session = session
        self.public = public
        self.private = private
        self.database = Database(f"sqlite:///{database}")
        self.filesystem = Filesystem(str(self.public))

    # Bytes

    # TODO: add read_file_text/data?
    def read_bytes(self, path: str) -> bytes:
        return self.filesystem.read_bytes(path)

    # File

    def copy_file(self, path: str, *, folder: Optional[str] = None) -> str:
        return self.filesystem.copy_file(path, folder=folder)

    def count_files(self):
        return len(self.filesystem.list_files())

    def create_file(self, path: str, *, folder: Optional[str] = None) -> str:
        resource = FileResource(path=path)
        name = str(path.split("/")[-1])
        if "?" in name:
            name = str(name.split("?")[0])
        if "." not in name:
            name = f"{name}.csv"
        return self.filesystem.create_file(
            name, bytes=resource.read_bytes(), folder=folder
        )

    def upload_file(
        self, name: str, *, bytes: bytes, folder: Optional[str] = None
    ) -> str:
        return self.filesystem.create_file(name, bytes=bytes, folder=folder)

    def delete_file(self, path: str) -> str:
        self.database.delete_record(path)
        self.filesystem.delete_file(path)
        return path

    # TODO: fix not safe
    def index_file(self, path: str) -> Optional[IFile]:
        file = self.read_file(path)
        if file:
            if not file.get("record"):
                resource = Resource(path=path, basepath=str(self.public))
                file["record"] = self.database.create_record(resource)
            return file

    def list_files(self) -> List[IFileItem]:
        records = self.database.list_records()
        mapping = {record["path"]: record for record in records}
        items = self.filesystem.list_files()
        for item in items:
            record = mapping.get(item["path"])
            if record and "errorCount" in record:
                item["errorCount"] = record["errorCount"]
        return items

    def move_file(self, path: str, *, folder: Optional[str] = None) -> str:
        source = path
        target = self.filesystem.move_file(path, folder=folder)
        self.database.move_record(source, target)
        return target

    def read_file(self, path: str) -> Optional[IFile]:
        item = self.filesystem.read_file(path)
        if item:
            file = IFile(**item)
            record = self.database.read_record(path)
            if record:
                file["record"] = record
            return file

    def rename_file(self, path: str, *, name: str) -> str:
        source = path
        target = self.filesystem.rename_file(path, name=name)
        self.database.move_record(source, target)
        return target

    def save_file(self, name: str, *, bytes: bytes, folder: Optional[str] = None) -> str:
        return self.filesystem.create_file(name, bytes=bytes, folder=folder)

    # TODO: implement
    def update_file(self, path: str):
        pass

    # Field

    def list_fields(self) -> List[IFieldItem]:
        return self.database.list_fields()

    # Folder

    def create_folder(self, name: str, *, folder: Optional[str] = None) -> str:
        return self.filesystem.create_folder(name, folder=folder)

    # Json

    def read_json(self, path: str) -> Any:
        path = self.filesystem.get_secure_fullpath(path)
        resource = JsonResource(path=path)
        return resource.read_json()

    def write_json(self, path: str, *, data: Any):
        path = self.filesystem.get_secure_fullpath(path)
        resource = JsonResource(data=data)
        resource.write_json(path=path)

    # Package

    def create_package(self):
        path = str(self.public / settings.PACKAGE_PATH)
        if not os.path.exists(path):
            helpers.write_file(path, json.dumps({"resource": []}))
        path = str(Path(path).relative_to(self.public))
        return path

    def publish_package(self, **params):
        response = {}
        controls = {
            "github": portals.GithubControl,
            "zenodo": portals.ZenodoControl,
            "ckan": portals.CkanControl,
        }
        control_type = params["type"]
        allow_update = params["allow_update"]

        del params["type"]
        del params["sandbox"]
        if not allow_update:
            del params["allow_update"]

        package = Package(str(self.public / settings.PACKAGE_PATH))
        if not package.name and not allow_update:
            now = datetime.datetime.now()
            date_time = now.strftime("%H-%M-%S")
            package.name = f"test_package_{date_time}"

        control = controls.get(control_type)
        if not control:
            response["error"] = "Matching control[Github|Zenodo|CKAN] not found"
            return response
        try:
            if "url" in params:
                target = params["url"]
                del params["url"]
                result = package.publish(target=target, control=control(**params))
            else:
                result = package.publish(control=control(**params))
            if control_type == "github":
                result = result.full_name
            response["url"] = result
        except FrictionlessException as exception:
            response["error"] = exception.error.message

        return response

    # Project

    def index_project(self):
        pass

    def query_project(self, query: str) -> IQueryData:
        return self.database.query(query)

    # Table

    def export_table(self, source: str, *, target: str) -> str:
        assert self.filesystem.is_filename(target)
        target = self.filesystem.get_secure_fullpath(target)
        source = self.filesystem.get_secure_fullpath(source)
        self.database.export_table(source, target=target)
        return target

    def query_table(self, query: str) -> ITable:
        return self.database.query_table(query)

    def read_table(
        self,
        path: str,
        *,
        valid: Optional[bool] = None,
        limit: Optional[int] = None,
        offset: Optional[int] = None,
    ) -> ITable:
        return self.database.read_table(path, valid=valid, limit=limit, offset=offset)

    def save_table(self, path: str, tablePatch: dict[str, str]) -> str:
        assert self.filesystem.is_filename(path)
        self.database.save_table(
            path, tablePatch=tablePatch, basepath=str(self.filesystem.basepath)
        )
        return path

    # Text

    # TODO: use detected resource.encoding if indexed
    def read_text(self, path: str) -> Any:
        path = self.filesystem.get_secure_fullpath(path)
        resource = TextResource(path=path)
        return resource.read_text()

    def write_text(self, path: str, *, text: str):
        path = self.filesystem.get_secure_fullpath(path)
        resource = TextResource(data=text)
        resource.write_text(path=path)
