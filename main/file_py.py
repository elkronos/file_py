import os
import shutil
import tempfile
import stat
import subprocess
from pathlib import Path
import re
import sys
from typing import Union, List, Callable, Optional, Any

# Conditional imports for Unix-specific modules
if os.name != 'nt':
    import pwd
    import grp

# Type alias for path-like objects
PathLike = Union[str, Path]

# Temporary files stack for cleanup
_temp_stack: List[Path] = []


# ------------------------------
# Helper Functions
# ------------------------------

def symbolic_to_octal(symbolic: str) -> int:
    """
    Convert symbolic permission string to octal.

    Parameters:
    - symbolic (str): Symbolic permission string (e.g., 'u+rwx,g+rx,o+r').

    Returns:
    - int: Octal representation of permissions.

    Raises:
    - ValueError: If the symbolic string is invalid.
    """
    perm = 0
    # Define permission bits
    perm_bits = {
        'u': {'r': stat.S_IRUSR, 'w': stat.S_IWUSR, 'x': stat.S_IXUSR},
        'g': {'r': stat.S_IRGRP, 'w': stat.S_IWGRP, 'x': stat.S_IXGRP},
        'o': {'r': stat.S_IROTH, 'w': stat.S_IWOTH, 'x': stat.S_IXOTH},
        'a': {'r': stat.S_IRUSR | stat.S_IRGRP | stat.S_IROTH,
              'w': stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH,
              'x': stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH},
    }
    # Split symbolic string by comma to handle multiple operations
    operations = symbolic.split(',')
    for op in operations:
        match = re.match(r'([ugoa]*)([+=-])([rwx]*)', op.strip())
        if not match:
            raise ValueError(f"Invalid symbolic permission: {op}")
        who, action, perms = match.groups()
        if not who:
            who = 'a'  # Default to 'all' if not specified
        for w in who:
            for p in perms:
                if p in perm_bits[w]:
                    if action == '+':
                        perm |= perm_bits[w][p]
                    elif action == '-':
                        perm &= ~perm_bits[w][p]
                    elif action == '=':
                        # Reset permissions for this category before setting
                        if w != 'a':
                            for key in perm_bits[w]:
                                perm &= ~perm_bits[w][key]
                        perm |= perm_bits[w][p]
    return perm


def human_to_bytes(s: str) -> int:
    """
    Convert a human-readable size string to bytes.

    Parameters:
    - s (str): Size string (e.g., '1KB', '5MB').

    Returns:
    - int: Size in bytes.

    Raises:
    - ValueError: If the format is invalid.
    """
    units = {"B":1, "K":1024, "KB":1024, "M":1024**2, "MB":1024**2, "G":1024**3, "GB":1024**3}
    match = re.match(r"(\d+)([KMGB]?B?)", s.upper())
    if not match:
        raise ValueError(f"Invalid size format: {s}")
    num, unit = match.groups()
    return int(num) * units.get(unit, 1)


# ------------------------------
# Copy Functions
# ------------------------------

def file_copy(path: PathLike, new_path: PathLike, overwrite: bool = False) -> Path:
    """
    Copy a file from `path` to `new_path`.

    Parameters:
    - path (str or Path): Source file path.
    - new_path (str or Path): Destination file path.
    - overwrite (bool): Whether to overwrite the destination if it exists.

    Returns:
    - Path: The new file path.

    Raises:
    - FileExistsError: If the destination exists and overwrite is False.
    - FileNotFoundError: If the source file does not exist.
    - IsADirectoryError: If the source path is a directory.
    """
    path = Path(path)
    new_path = Path(new_path)
    if not path.is_file():
        raise IsADirectoryError(f"Source {path} is not a file.")
    if new_path.exists() and not overwrite:
        raise FileExistsError(f"Destination {new_path} already exists.")
    shutil.copy2(path, new_path)
    return new_path


def link_copy(path: PathLike, new_path: PathLike, overwrite: bool = False) -> Path:
    """
    Copy a symbolic link from `path` to `new_path`.

    Parameters:
    - path (str or Path): Source symlink path.
    - new_path (str or Path): Destination symlink path.
    - overwrite (bool): Whether to overwrite the destination if it exists.

    Returns:
    - Path: The new symlink path.

    Raises:
    - FileExistsError: If the destination exists and overwrite is False.
    - FileNotFoundError: If the source symlink does not exist.
    - ValueError: If the source path is not a symlink.
    """
    path = Path(path)
    new_path = Path(new_path)
    if not path.is_symlink():
        raise ValueError(f"Source {path} is not a symbolic link.")
    if new_path.exists():
        if overwrite:
            new_path.unlink()
        else:
            raise FileExistsError(f"Destination {new_path} already exists.")
    target = os.readlink(path)
    os.symlink(target, new_path)
    return new_path


def dir_copy(path: PathLike, new_path: PathLike, overwrite: bool = False) -> Path:
    """
    Recursively copy a directory from `path` to `new_path`.

    Parameters:
    - path (str or Path): Source directory path.
    - new_path (str or Path): Destination directory path.
    - overwrite (bool): Whether to overwrite the destination if it exists.

    Returns:
    - Path: The new directory path.

    Raises:
    - FileExistsError: If the destination exists and overwrite is False.
    - FileNotFoundError: If the source directory does not exist.
    - NotADirectoryError: If the source path is not a directory.
    """
    path = Path(path)
    new_path = Path(new_path)
    if not path.is_dir():
        raise NotADirectoryError(f"Source {path} is not a directory.")
    if new_path.exists():
        if overwrite:
            shutil.rmtree(new_path)
        else:
            raise FileExistsError(f"Destination directory {new_path} already exists.")
    shutil.copytree(path, new_path)
    return new_path


# ------------------------------
# Create Functions
# ------------------------------

def file_create(path: PathLike, mode: str = "u+rw,g+r,o+r") -> Path:
    """
    Create a new file at `path` with the specified permissions.

    Parameters:
    - path (str or Path): The file path.
    - mode (str): Permissions in symbolic form (e.g., 'u+rwx,g+rx,o+r').

    Returns:
    - Path: The created file path.

    Raises:
    - ValueError: If the permission string is invalid.
    - OSError: If file creation or permission setting fails.
    """
    path = Path(path)
    path.touch(exist_ok=True)
    file_chmod(path, mode)
    return path


def dir_create(path: PathLike, mode: str = "u+rwx,g+rx,o+rx", recurse: bool = True) -> Path:
    """
    Create a new directory at `path` with the specified permissions.

    Parameters:
    - path (str or Path): The directory path.
    - mode (str): Permissions in symbolic form (e.g., 'u+rwx,g+rx,o+rx').
    - recurse (bool): Whether to create parent directories as needed.

    Returns:
    - Path: The created directory path.

    Raises:
    - ValueError: If the permission string is invalid.
    - OSError: If directory creation or permission setting fails.
    """
    path = Path(path)
    path.mkdir(parents=recurse, exist_ok=True)
    file_chmod(path, mode)
    return path


def link_create(target: PathLike, new_path: PathLike, symbolic: bool = True) -> Path:
    """
    Create a symbolic or hard link from `target` to `new_path`.

    Parameters:
    - target (str or Path): The target file or directory.
    - new_path (str or Path): The new link path.
    - symbolic (bool): Whether to create a symbolic link. Hard links are not supported on all platforms.

    Returns:
    - Path: The created link path.

    Raises:
    - NotImplementedError: If hard links are not supported on the platform.
    - FileExistsError: If the destination exists.
    - OSError: If link creation fails.
    """
    target = Path(target)
    new_path = Path(new_path)
    if new_path.exists():
        raise FileExistsError(f"Destination {new_path} already exists.")
    if symbolic:
        os.symlink(target, new_path)
    else:
        if os.name == 'nt':
            raise NotImplementedError("Hard links are not supported on Windows.")
        os.link(target, new_path)
    return new_path


# ------------------------------
# Delete Functions
# ------------------------------

def file_delete(path: PathLike) -> Path:
    """
    Delete a file or symbolic link at `path`.

    Parameters:
    - path (str or Path): The file or symlink path.

    Returns:
    - Path: The deleted path.

    Raises:
    - FileNotFoundError: If the path does not exist.
    - IsADirectoryError: If the path is a directory.
    - OSError: If deletion fails.
    """
    path = Path(path)
    if path.is_file() or path.is_symlink():
        path.unlink()
    elif path.is_dir():
        raise IsADirectoryError(f"Path {path} is a directory. Use dir_delete to remove directories.")
    else:
        raise FileNotFoundError(f"Path {path} does not exist.")
    return path


def dir_delete(path: PathLike) -> Path:
    """
    Recursively delete a directory at `path`.

    Parameters:
    - path (str or Path): The directory path.

    Returns:
    - Path: The deleted directory path.

    Raises:
    - NotADirectoryError: If the path is not a directory.
    - FileNotFoundError: If the directory does not exist.
    - OSError: If deletion fails.
    """
    path = Path(path)
    if path.is_dir():
        shutil.rmtree(path)
    else:
        raise NotADirectoryError(f"Path {path} is not a directory.")
    return path


def link_delete(path: PathLike) -> Path:
    """
    Delete a symbolic link at `path`.

    Parameters:
    - path (str or Path): The symlink path.

    Returns:
    - Path: The deleted symlink path.

    Raises:
    - ValueError: If the path is not a symbolic link.
    - FileNotFoundError: If the symlink does not exist.
    - OSError: If deletion fails.
    """
    path = Path(path)
    if path.is_symlink():
        path.unlink()
    else:
        raise ValueError(f"Path {path} is not a symbolic link.")
    return path


# ------------------------------
# Directory Listing Functions
# ------------------------------

def dir_ls(
    path: PathLike = ".",
    all: bool = False,
    recurse: bool = False,
    type_filter: str = "any",
    glob: Optional[str] = None,
    regexp: Optional[str] = None,
    invert: bool = False,
    fail: bool = True
) -> List[Path]:
    """
    List directory contents with various filtering options.

    Parameters:
    - path (str or Path): The directory path.
    - all (bool): Include hidden files.
    - recurse (bool): Recursively list directories.
    - type_filter (str): Filter by type ('any', 'file', 'directory', 'symlink').
    - glob (str): Glob pattern to filter paths.
    - regexp (str): Regular expression to filter paths.
    - invert (bool): Invert the filter conditions.
    - fail (bool): Raise an error if the path does not exist.

    Returns:
    - List[Path]: A list of Path objects matching the criteria.

    Raises:
    - FileNotFoundError: If the path does not exist and fail is True.
    - ValueError: If type_filter is invalid.
    - OSError: If directory listing fails.
    """
    path = Path(path)
    if not path.exists():
        if fail:
            raise FileNotFoundError(f"Path {path} does not exist.")
        else:
            return []
    if not path.is_dir():
        raise NotADirectoryError(f"Path {path} is not a directory.")

    results = []
    try:
        iterator = path.rglob('*') if recurse else path.iterdir()
        for p in iterator:
            # Hidden files
            if not all and p.name.startswith('.'):
                continue

            # Type filtering
            if type_filter != "any":
                if type_filter == "file" and not p.is_file():
                    continue
                elif type_filter == "directory" and not p.is_dir():
                    continue
                elif type_filter == "symlink" and not p.is_symlink():
                    continue
                else:
                    raise ValueError(f"Invalid type_filter: {type_filter}")

            # Glob filtering
            if glob:
                if p.match(glob):
                    condition = not invert
                else:
                    condition = invert
                if not condition:
                    continue

            # Regexp filtering
            if regexp:
                if re.search(regexp, str(p)):
                    condition = not invert
                else:
                    condition = invert
                if not condition:
                    continue

            results.append(p)
    except Exception as e:
        raise RuntimeError(f"Failed to list directory {path}: {e}")

    return results


def dir_info(
    path: PathLike = ".",
    all: bool = False,
    recurse: bool = False,
    type_filter: str = "any",
    glob: Optional[str] = None,
    regexp: Optional[str] = None,
    invert: bool = False,
    fail: bool = True
) -> List[dict]:
    """
    Retrieve detailed information about directory contents.

    Parameters:
    - All parameters are the same as `dir_ls`.

    Returns:
    - List[dict]: A list of dictionaries containing file metadata.

    Raises:
    - Same as `dir_ls`.
    """
    paths = dir_ls(path, all, recurse, type_filter, glob, regexp, invert, fail)
    info = []
    for p in paths:
        try:
            stat_info = p.lstat()
            if os.name != 'nt':
                user = grp.getgrgid(stat_info.st_gid).gr_name if hasattr(grp, 'getgrgid') else None
                group = pwd.getpwuid(stat_info.st_uid).pw_name if hasattr(pwd, 'getpwuid') else None
            else:
                user = group = None
            info.append({
                "path": str(p),
                "type": "directory" if p.is_dir() else "file" if p.is_file() else "symlink",
                "size": stat_info.st_size,
                "permissions": stat.S_IMODE(stat_info.st_mode),
                "modification_time": stat_info.st_mtime,
                "user": user,
                "group": group,
                "device_id": stat_info.st_dev,
                "hard_links": stat_info.st_nlink,
                "inode": stat_info.st_ino,
                "access_time": stat_info.st_atime,
                "change_time": stat_info.st_ctime,
                "birth_time": getattr(stat_info, 'st_birthtime', None)
            })
        except Exception as e:
            # Optionally log the error or skip
            continue
    return info


def dir_map(
    path: PathLike = ".",
    fun: Callable[[Path], Any] = lambda x: x,
    all: bool = False,
    recurse: bool = False,
    type_filter: str = "any",
    fail: bool = True
) -> List[Any]:
    """
    Apply a function to each item in a directory listing.

    Parameters:
    - path (str or Path): The directory path.
    - fun (callable): Function to apply to each Path object.
    - All parameters are the same as `dir_ls`.

    Returns:
    - List: Results of applying `fun` to each Path.

    Raises:
    - Same as `dir_ls`.
    """
    paths = dir_ls(path, all, recurse, type_filter, glob=None, regexp=None, invert=False, fail=fail)
    return [fun(p) for p in paths]


def dir_walk(
    path: PathLike = ".",
    fun: Callable[[Path], None] = lambda x: None,
    all: bool = False,
    recurse: bool = False,
    type_filter: str = "any",
    fail: bool = True
) -> Path:
    """
    Walk through directory contents and apply a function for its side effects.

    Parameters:
    - path (str or Path): The directory path.
    - fun (callable): Function to apply to each Path object.
    - All parameters are the same as `dir_ls`.

    Returns:
    - Path: The original directory path.

    Raises:
    - Same as `dir_ls`.
    """
    paths = dir_ls(path, all, recurse, type_filter, glob=None, regexp=None, invert=False, fail=fail)
    for p in paths:
        fun(p)
    return Path(path)


# ------------------------------
# File Access Functions
# ------------------------------

def file_access(path: PathLike, mode: str = "exists") -> bool:
    """
    Check access permissions for a file.

    Parameters:
    - path (str or Path): The file path.
    - mode (str): Permissions to check ('exists', 'read', 'write', 'execute').

    Returns:
    - bool: True if all specified permissions are granted, False otherwise.

    Raises:
    - ValueError: If an invalid mode is specified.
    """
    path = Path(path)
    mode_map = {
        "exists": os.F_OK,
        "read": os.R_OK,
        "write": os.W_OK,
        "execute": os.X_OK
    }
    modes = []
    for m in mode.split():
        if m in mode_map:
            modes.append(mode_map[m])
        else:
            raise ValueError(f"Invalid mode: {m}")
    return all(os.access(path, m) for m in modes)


def file_exists(path: PathLike) -> bool:
    """
    Check if a file exists.

    Parameters:
    - path (str or Path): The file path.

    Returns:
    - bool: True if the file exists, False otherwise.
    """
    return Path(path).exists()


def dir_exists(path: PathLike) -> bool:
    """
    Check if a directory exists.

    Parameters:
    - path (str or Path): The directory path.

    Returns:
    - bool: True if the directory exists, False otherwise.
    """
    return Path(path).is_dir()


def link_exists(path: PathLike) -> bool:
    """
    Check if a symbolic link exists.

    Parameters:
    - path (str or Path): The symlink path.

    Returns:
    - bool: True if the symlink exists, False otherwise.
    """
    return Path(path).is_symlink()


# ------------------------------
# Permission Functions
# ------------------------------

def file_chmod(path: PathLike, mode: Union[str, int]) -> Path:
    """
    Change the permissions of a file or directory.

    Parameters:
    - path (str or Path): The file or directory path.
    - mode (str or int): Permissions in symbolic (e.g., 'u+rwx,g+rx,o+r') or octal form (e.g., 0o755).

    Returns:
    - Path: The path with updated permissions.

    Raises:
    - ValueError: If the symbolic permission string is invalid.
    - OSError: If permission change fails.
    """
    path = Path(path)
    if isinstance(mode, str):
        mode = symbolic_to_octal(mode)
    os.chmod(path, mode)
    return path


def file_chown(path: PathLike, user_id: Optional[Union[str, int]] = None, group_id: Optional[Union[str, int]] = None) -> Path:
    """
    Change the owner and/or group of a file.

    Parameters:
    - path (str or Path): The file path.
    - user_id (str or int, optional): New owner username or UID.
    - group_id (str or int, optional): New group name or GID.

    Returns:
    - Path: The file path.

    Raises:
    - NotImplementedError: If called on Windows.
    - ValueError: If provided user_id or group_id is invalid.
    - KeyError: If username or group name does not exist.
    - OSError: If ownership change fails.
    """
    if os.name == 'nt':
        raise NotImplementedError("file_chown is not supported on Windows.")
    
    path = Path(path)
    uid = -1
    gid = -1
    if user_id is not None:
        if isinstance(user_id, int):
            uid = user_id
        elif isinstance(user_id, str) and hasattr(pwd, 'getpwnam'):
            try:
                uid = pwd.getpwnam(user_id).pw_uid
            except KeyError:
                raise ValueError(f"User '{user_id}' does not exist.")
        else:
            raise ValueError("Invalid user_id provided.")
    if group_id is not None:
        if isinstance(group_id, int):
            gid = group_id
        elif isinstance(group_id, str) and hasattr(grp, 'getgrnam'):
            try:
                gid = grp.getgrnam(group_id).gr_gid
            except KeyError:
                raise ValueError(f"Group '{group_id}' does not exist.")
        else:
            raise ValueError("Invalid group_id provided.")
    os.chown(path, uid, gid)
    return path


# ------------------------------
# File Information Functions
# ------------------------------

def file_info(path: PathLike, fail: bool = True, follow: bool = False) -> Optional[dict]:
    """
    Retrieve metadata information about a file.

    Parameters:
    - path (str or Path): The file path.
    - fail (bool): Whether to raise an exception on failure.
    - follow (bool): Whether to follow symbolic links.

    Returns:
    - dict or None: A dictionary containing file metadata, or None if fail is False and an error occurs.

    Raises:
    - OSError: If retrieving file info fails and fail is True.
    """
    path = Path(path)
    try:
        stat_info = path.stat() if follow else path.lstat()
        if os.name != 'nt':
            user = grp.getgrgid(stat_info.st_gid).gr_name if hasattr(grp, 'getgrgid') else None
            group = pwd.getpwuid(stat_info.st_uid).pw_name if hasattr(pwd, 'getpwuid') else None
        else:
            user = group = None
        return {
            "path": str(path),
            "type": "directory" if path.is_dir() else "file" if path.is_file() else "symlink",
            "size": stat_info.st_size,
            "permissions": stat.S_IMODE(stat_info.st_mode),
            "modification_time": stat_info.st_mtime,
            "user": user,
            "group": group,
            "device_id": stat_info.st_dev,
            "hard_links": stat_info.st_nlink,
            "inode": stat_info.st_ino,
            "access_time": stat_info.st_atime,
            "change_time": stat_info.st_ctime,
            "birth_time": getattr(stat_info, 'st_birthtime', None)
        }
    except Exception as e:
        if fail:
            raise e
        else:
            return None


def file_size(path: PathLike, fail: bool = True) -> Optional[int]:
    """
    Get the size of a file.

    Parameters:
    - path (str or Path): The file path.
    - fail (bool): Whether to raise an exception on failure.

    Returns:
    - int or None: Size of the file in bytes, or None if not found and fail is False.

    Raises:
    - Same as `file_info` when fail is True.
    """
    info = file_info(path, fail, follow=True)
    return info['size'] if info else None


# ------------------------------
# Move or Rename Functions
# ------------------------------

def file_move(path: PathLike, new_path: PathLike) -> Path:
    """
    Move or rename a file or directory.

    Parameters:
    - path (str or Path): Source path.
    - new_path (str or Path): Destination path.

    Returns:
    - Path: The new path.

    Raises:
    - FileNotFoundError: If the source path does not exist.
    - OSError: If move operation fails.
    """
    path = Path(path)
    new_path = Path(new_path)
    shutil.move(str(path), str(new_path))
    return new_path


# ------------------------------
# Open Files or Directories
# ------------------------------

def file_show(path: PathLike, browser: Optional[str] = None) -> Path:
    """
    Open a file or directory using the default system application or a specified browser.

    Parameters:
    - path (str or Path): The file or directory path.
    - browser (str, optional): The browser executable to use.

    Returns:
    - Path: The path that was opened.

    Raises:
    - FileNotFoundError: If the path does not exist.
    - RuntimeError: If opening the path fails.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Path {path} does not exist.")
    try:
        if browser:
            subprocess.run([browser, str(path)], check=True)
        else:
            if os.name == 'nt':
                os.startfile(str(path))
            elif sys.platform == 'darwin':
                subprocess.run(['open', str(path)], check=True)
            else:
                subprocess.run(['xdg-open', str(path)], check=True)
    except Exception as e:
        raise RuntimeError(f"Failed to open {path}: {e}")
    return path


# ------------------------------
# Temporary Files Functions
# ------------------------------

def file_temp(pattern: str = "file", tmp_dir: Optional[PathLike] = None, ext: str = "") -> Path:
    """
    Create a temporary file.

    Parameters:
    - pattern (str): Prefix for the temporary file name.
    - tmp_dir (str or Path, optional): Directory to create the temp file in.
    - ext (str): File extension.

    Returns:
    - Path: The path to the temporary file.
    """
    tmp_dir = Path(tmp_dir) if tmp_dir else Path(tempfile.gettempdir())
    suffix = f".{ext}" if ext else ""
    fd, path = tempfile.mkstemp(prefix=pattern, suffix=suffix, dir=str(tmp_dir))
    os.close(fd)
    return Path(path)


def file_temp_push(path: PathLike) -> Path:
    """
    Push a path onto the temporary stack for later cleanup.

    Parameters:
    - path (str or Path): The path to push.

    Returns:
    - Path: The pushed path.
    """
    global _temp_stack
    _temp_stack.append(Path(path))
    return Path(path)


def file_temp_pop() -> Optional[Path]:
    """
    Pop the last path from the temporary stack.

    Returns:
    - Path or None: The popped path or None if the stack is empty.
    """
    global _temp_stack
    if _temp_stack:
        return _temp_stack.pop()
    return None


def path_temp(*args: str) -> Path:
    """
    Construct a path within the temporary directory.

    Parameters:
    - *args (str): Additional path components.

    Returns:
    - Path: The constructed temporary path.
    """
    return Path(tempfile.gettempdir(), *args)


# ------------------------------
# Touch Files
# ------------------------------

def file_touch(
    path: PathLike,
    access_time: Optional[float] = None,
    modification_time: Optional[float] = None
) -> Path:
    """
    Update the access and modification times of a file.

    Parameters:
    - path (str or Path): The file path.
    - access_time (float or int, optional): New access time as a timestamp.
    - modification_time (float or int, optional): New modification time as a timestamp.

    Returns:
    - Path: The touched file path.

    Raises:
    - FileNotFoundError: If the path does not exist.
    - OSError: If updating times fails.
    """
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(f"Path {path} does not exist.")
    atime = access_time if access_time is not None else os.path.getatime(path)
    mtime = modification_time if modification_time is not None else os.path.getmtime(path)
    os.utime(path, (atime, mtime))
    return path


# ------------------------------
# File System Utilities
# ------------------------------

def fs_bytes(x: Union[str, int]) -> int:
    """
    Convert a human-readable size string to bytes.

    Parameters:
    - x (str or int): Size string (e.g., '1KB', '5MB') or integer bytes.

    Returns:
    - int: Size in bytes.

    Raises:
    - ValueError: If the size string format is invalid.
    """
    if isinstance(x, str):
        return human_to_bytes(x)
    elif isinstance(x, int):
        return x
    else:
        raise TypeError("fs_bytes expects a string or integer.")


def fs_path(x: PathLike) -> Path:
    """
    Convert a string to a Path object.

    Parameters:
    - x (str or Path): The path.

    Returns:
    - Path: The Path object.
    """
    return Path(x)


def fs_perms(x: Union[str, int]) -> int:
    """
    Convert symbolic permissions to octal.

    Parameters:
    - x (str or int): Symbolic permissions (e.g., 'u+rwx') or octal.

    Returns:
    - int: Octal permissions.

    Raises:
    - ValueError: If symbolic permission string is invalid.
    - TypeError: If the input type is not supported.
    """
    if isinstance(x, str):
        return symbolic_to_octal(x)
    elif isinstance(x, int):
        return x
    else:
        raise TypeError("fs_perms expects a string or integer.")


# ------------------------------
# User and Group ID Functions
# ------------------------------

def group_ids() -> List[dict]:
    """
    Retrieve a list of all groups on the system.

    Returns:
    - List[dict]: Each dictionary contains 'name' and 'gid'.
    """
    if os.name != 'nt' and hasattr(grp, 'getgrall'):
        groups = grp.getgrall()
        return [{"name": g.gr_name, "gid": g.gr_gid} for g in groups]
    return []


def user_ids() -> List[dict]:
    """
    Retrieve a list of all users on the system.

    Returns:
    - List[dict]: Each dictionary contains 'name' and 'uid'.
    """
    if os.name != 'nt' and hasattr(pwd, 'getpwall'):
        users = pwd.getpwall()
        return [{"name": u.pw_name, "uid": u.pw_uid} for u in users]
    return []


# ------------------------------
# Path Functions
# ------------------------------

def is_absolute_path(path: PathLike) -> bool:
    """
    Check if a path is absolute.

    Parameters:
    - path (str or Path): The path to check.

    Returns:
    - bool: True if absolute, False otherwise.
    """
    return Path(path).is_absolute()


def is_file(path: PathLike, follow: bool = True) -> bool:
    """
    Check if a path is a file.

    Parameters:
    - path (str or Path): The path to check.
    - follow (bool): Whether to follow symbolic links.

    Returns:
    - bool: True if a file, False otherwise.
    """
    p = Path(path)
    return p.is_file() if follow else (p.is_symlink() and p.resolve(strict=False).is_file())


def is_dir(path: PathLike, follow: bool = True) -> bool:
    """
    Check if a path is a directory.

    Parameters:
    - path (str or Path): The path to check.
    - follow (bool): Whether to follow symbolic links.

    Returns:
    - bool: True if a directory, False otherwise.
    """
    p = Path(path)
    return p.is_dir() if follow else (p.is_symlink() and p.resolve(strict=False).is_dir())


def is_link(path: PathLike) -> bool:
    """
    Check if a path is a symbolic link.

    Parameters:
    - path (str or Path): The path to check.

    Returns:
    - bool: True if a symbolic link, False otherwise.
    """
    return Path(path).is_symlink()


def is_file_empty(path: PathLike, follow: bool = True) -> bool:
    """
    Check if a file is empty.

    Parameters:
    - path (str or Path): The file path.
    - follow (bool): Whether to follow symbolic links.

    Returns:
    - bool: True if empty, False otherwise.
    """
    p = Path(path)
    if not p.exists():
        return False
    if follow and p.is_symlink():
        p = p.resolve(strict=False)
    if p.is_file():
        return p.stat().st_size == 0
    return False


def link_path(path: PathLike) -> Path:
    """
    Get the target path of a symbolic link.

    Parameters:
    - path (str or Path): The symlink path.

    Returns:
    - Path: The target path.

    Raises:
    - ValueError: If the path is not a symlink.
    - OSError: If reading the symlink target fails.
    """
    p = Path(path)
    if not p.is_symlink():
        raise ValueError(f"Path {path} is not a symbolic link.")
    target = os.readlink(p)
    return Path(target)


def path_construct(*args: str, ext: str = "") -> Path:
    """
    Construct a path from components with an optional extension.

    Parameters:
    - *args (str): Path components.
    - ext (str): File extension.

    Returns:
    - Path: The constructed path.
    """
    path = Path(*args)
    return path.with_suffix(f".{ext}") if ext else path


def path_expand(path: PathLike) -> Path:
    """
    Expand user home directory symbols in a path.

    Parameters:
    - path (str or Path): The path to expand.

    Returns:
    - Path: The expanded path.
    """
    return Path(path).expanduser()


def path_home(*args: str) -> Path:
    """
    Construct a path within the user's home directory.

    Parameters:
    - *args (str): Additional path components.

    Returns:
    - Path: The constructed path.
    """
    return Path.home().joinpath(*args)


def path_home_r(*args: str) -> Path:
    """
    Construct a path within the user's home directory using R's definition.

    Parameters:
    - *args (str): Additional path components.

    Returns:
    - Path: The constructed path.
    """
    return Path(os.path.expanduser("~")).joinpath(*args)


def path_file(path: PathLike) -> str:
    """
    Get the filename component of a path.

    Parameters:
    - path (str or Path): The path.

    Returns:
    - str: The filename.
    """
    return Path(path).name


def path_dir(path: PathLike) -> Path:
    """
    Get the directory component of a path.

    Parameters:
    - path (str or Path): The path.

    Returns:
    - Path: The parent directory.
    """
    return Path(path).parent


def path_ext(path: PathLike) -> str:
    """
    Get the file extension of a path.

    Parameters:
    - path (str or Path): The path.

    Returns:
    - str: The file extension.
    """
    return Path(path).suffix


def path_ext_remove(path: PathLike) -> Path:
    """
    Remove the file extension from a path.

    Parameters:
    - path (str or Path): The path.

    Returns:
    - Path: The path without the extension.
    """
    return Path(path).with_suffix('')


def path_ext_set(path: PathLike, ext: str) -> Path:
    """
    Set a new file extension for a path.

    Parameters:
    - path (str or Path): The path.
    - ext (str): The new extension.

    Returns:
    - Path: The path with the new extension.
    """
    return Path(path).with_suffix(f".{ext}")


def path_filter(
    paths: List[PathLike],
    glob: Optional[str] = None,
    regexp: Optional[str] = None,
    invert: bool = False
) -> List[Path]:
    """
    Filter a list of paths based on glob and regex patterns.

    Parameters:
    - paths (List[str or Path]): The list of paths to filter.
    - glob (str, optional): Glob pattern to filter paths.
    - regexp (str, optional): Regular expression to filter paths.
    - invert (bool): Invert the filter conditions.

    Returns:
    - List[Path]: The filtered list of paths.
    """
    filtered = []
    for path in paths:
        p = Path(path)
        match = True
        if glob:
            if p.match(glob):
                match = not invert
            else:
                match = invert
        if regexp:
            if re.search(regexp, str(p)):
                match = not invert
            else:
                match = invert
        if match:
            filtered.append(p)
    return filtered


def path_real(path: PathLike) -> Path:
    """
    Get the canonical path, resolving any symlinks and relative components.

    Parameters:
    - path (str or Path): The path to resolve.

    Returns:
    - Path: The resolved path.
    """
    return Path(path).resolve()


def path_split(path: PathLike) -> List[str]:
    """
    Split a path into its components.

    Parameters:
    - path (str or Path): The path to split.

    Returns:
    - List[str]: The path components.
    """
    return list(Path(path).parts)


def path_join(*parts: str) -> Path:
    """
    Join multiple path components into a single path.

    Parameters:
    - *parts (str): Path components.

    Returns:
    - Path: The joined path.
    """
    return Path(*parts)


def path_abs(path: PathLike, start: PathLike = ".") -> Path:
    """
    Get the absolute path, optionally relative to a starting directory.

    Parameters:
    - path (str or Path): The path to convert.
    - start (str or Path): The starting directory.

    Returns:
    - Path: The absolute path.
    """
    return Path(path).resolve(strict=False)


def path_norm(path: PathLike) -> str:
    """
    Normalize a path by resolving '.', '..', and symlinks.

    Parameters:
    - path (str or Path): The path to normalize.

    Returns:
    - str: The normalized path as a POSIX string.
    """
    return Path(path).resolve().as_posix()


def path_rel(path: PathLike, start: PathLike = ".") -> Path:
    """
    Compute the relative path from `start` to `path`.

    Parameters:
    - path (str or Path): The target path.
    - start (str or Path): The starting directory.

    Returns:
    - Path: The relative path.

    Note:
    - If `path` does not share a common ancestor with `start`, returns the absolute path.
    """
    try:
        return Path(path).relative_to(start)
    except ValueError:
        # If paths do not share a common ancestor, return the absolute path
        return Path(path).resolve()


def path_common(paths: List[PathLike]) -> str:
    """
    Find the common ancestor path of multiple paths.

    Parameters:
    - paths (List[str or Path]): The list of paths.

    Returns:
    - str: The common ancestor path.

    Raises:
    - ValueError: If no common ancestor exists.
    """
    return os.path.commonpath([str(p) for p in paths])


def path_has_parent(path: PathLike, parent: PathLike) -> bool:
    """
    Determine if `path` has `parent` as one of its ancestors.

    Parameters:
    - path (str or Path): The path to check.
    - parent (str or Path): The parent path.

    Returns:
    - bool: True if `parent` is an ancestor of `path`, False otherwise.
    """
    return Path(parent) in Path(path).parents


def path_package(package: str, *args: str) -> Path:
    """
    Construct a path within an installed Python package.

    Parameters:
    - package (str): The package name.
    - *args (str): Additional path components within the package.

    Returns:
    - Path: The constructed path.

    Raises:
    - ImportError: If the package does not exist.
    - AttributeError: If the package does not have a `__file__` attribute.
    """
    import importlib
    try:
        pkg = importlib.import_module(package)
        pkg_path = Path(pkg.__file__).parent
        return pkg_path.joinpath(*args)
    except ImportError:
        raise ImportError(f"Package '{package}' does not exist.")
    except AttributeError:
        raise AttributeError(f"Package '{package}' does not have a __file__ attribute.")


def path_sanitize(filename: str, replacement: str = "") -> str:
    """
    Sanitize a filename by removing invalid characters and reserved names.

    Parameters:
    - filename (str): The filename to sanitize.
    - replacement (str): The string to replace invalid characters with.

    Returns:
    - str: The sanitized filename.
    """
    sanitized = re.sub(r'[^a-zA-Z0-9_.-]', replacement, filename)
    sanitized = re.sub(r'\.{2,}', replacement, sanitized)
    reserved = {"CON", "PRN", "AUX", "NUL",
                "COM1", "COM2", "COM3", "COM4", "COM5", "COM6", "COM7", "COM8", "COM9",
                "LPT1", "LPT2", "LPT3", "LPT4", "LPT5", "LPT6", "LPT7", "LPT8", "LPT9"}
    if sanitized.upper() in reserved:
        sanitized = replacement
    return sanitized[:255]


def path_tidy(path: PathLike) -> str:
    """
    Tidy a path by resolving it and converting to POSIX format.

    Parameters:
    - path (str or Path): The path to tidy.

    Returns:
    - str: The tidied path.
    """
    return Path(path).resolve().as_posix()


# ------------------------------
# Additional Enhancements
# ------------------------------

def cleanup_temp_files() -> None:
    """
    Clean up all paths stored in the temporary stack.

    This function attempts to delete each path in the stack. It should be called
    when temporary files or directories are no longer needed.

    Raises:
    - OSError: If deletion of any path fails.
    """
    global _temp_stack
    while _temp_stack:
        path = _temp_stack.pop()
        try:
            if path.is_file() or path.is_symlink():
                path.unlink()
            elif path.is_dir():
                shutil.rmtree(path)
        except Exception as e:
            print(f"Failed to delete temporary path {path}: {e}", file=sys.stderr)


# Register cleanup function to be called upon program exit
import atexit
atexit.register(cleanup_temp_files)


# ------------------------------
# Example Usage (Commented Out)
# ------------------------------

# Example usage (uncomment to use)
# file_create("foo", mode="u+rw,g+r,o+r")
# file_copy("foo", "bar", overwrite=True)
# link_create("bar", "baz", symbolic=True)
# file_delete("bar")
# dir_create("dir", mode="u+rwx,g+rx,o+rx")
# dir_copy("dir", "dir_copy", overwrite=True)
# dir_delete("dir_copy")
