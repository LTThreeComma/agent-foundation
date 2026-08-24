use std::{
    collections::{BTreeMap, BTreeSet},
    error::Error,
    fmt, fs,
    path::{Component, Path, PathBuf},
    sync::{
        Arc, Mutex, PoisonError,
        atomic::{AtomicBool, Ordering},
    },
};

#[cfg(any(target_os = "linux", target_os = "macos"))]
use std::ffi::CString;
#[cfg(unix)]
use std::os::unix::{ffi::OsStrExt, io::AsRawFd};

use cap_std::{ambient_authority, fs::Dir};

use crate::{
    config::{Config, TrustedMountConfig},
    eip::{EIPPath, MountDescriptor, ResourceAuthority},
};

const READ_OPERATIONS: &[&str] = &["stat", "read_text", "open_reader", "list"];
const WRITE_OPERATIONS: &[&str] = &[
    "write_text",
    "open_writer",
    "mkdir",
    "patch_text",
    "copy",
    "move",
    "remove",
];
const OPTIONAL_OPERATIONS: &[&str] = &["find", "search", "command_cwd", "executable_source"];
const CANDIDATE_PREFIX: &str = ".eip-stage-";
const CANDIDATE_RANDOM_BYTES: usize = 16;

#[derive(Clone)]
pub(crate) struct MountRegistry {
    mounts: BTreeMap<String, Arc<Mount>>,
    root_mount_id: Option<String>,
}

pub(crate) struct Mount {
    pub(crate) mount_id: String,
    pub(crate) native_root: PathBuf,
    pub(crate) root: Arc<Dir>,
    pub(crate) writable: bool,
    pub(crate) allow_command_execution: bool,
    pub(crate) max_file_bytes: u64,
    allowed_operations: BTreeSet<String>,
    cleanup_fault: AtomicBool,
    staging_quota: StagingQuota,
}

pub(crate) struct OpenedFile {
    pub(crate) file: fs::File,
    pub(crate) metadata: fs::Metadata,
}

#[derive(Clone)]
pub(crate) struct CommandCwd {
    mount: Arc<Mount>,
    relative: PathBuf,
    pub(crate) native_path: PathBuf,
}

pub(crate) struct StagedCandidate {
    pub(crate) name: String,
    pub(crate) file: fs::File,
    mount: Arc<Mount>,
    destination: EIPPath,
    destination_name: std::ffi::OsString,
    parent: Dir,
    reservation: StagingReservation,
    removed: bool,
}

#[derive(Clone)]
struct StagingQuota {
    inner: Arc<StagingQuotaInner>,
}

struct StagingQuotaInner {
    state: Mutex<StagingQuotaState>,
    max_bytes: u64,
    max_objects: u64,
}

#[derive(Default)]
struct StagingQuotaState {
    bytes: u64,
    objects: u64,
}

struct StagingReservation {
    quota: StagingQuota,
    bytes: u64,
    state: ReservationState,
}

#[derive(Clone, Copy, PartialEq, Eq)]
enum ReservationState {
    Active,
    Released,
    Retained,
}

impl MountRegistry {
    pub(crate) fn initialize_scoped(config: &Config) -> Result<Self, MountInitError> {
        let prepared = config
            .mounts
            .iter()
            .map(PreparedMount::new)
            .collect::<Result<Vec<_>, _>>()?;
        validate_topology(&prepared)?;
        Self::from_prepared(config, prepared, config.root_mount_id.as_deref())
    }

    pub(crate) fn initialize_server(config: &Config) -> Result<Option<Self>, MountInitError> {
        if config.resource_authority != ResourceAuthority::Server {
            return Ok(None);
        }
        #[cfg(unix)]
        {
            let configured = TrustedMountConfig {
                mount_id: "server-root".to_owned(),
                native_root: PathBuf::from("/"),
                writable: true,
                allow_command_execution: true,
                max_file_bytes: config.limits.max_staged_file_bytes,
                allowed_operations: READ_OPERATIONS
                    .iter()
                    .chain(WRITE_OPERATIONS)
                    .chain(OPTIONAL_OPERATIONS)
                    .map(|value| (*value).to_owned())
                    .collect(),
            };
            let prepared = vec![PreparedMount::new(&configured)?];
            Self::from_prepared(config, prepared, Some("server-root")).map(Some)
        }
        #[cfg(not(unix))]
        {
            Err(MountInitError::new(
                "server resource authority is unsupported on this platform",
            ))
        }
    }

    fn from_prepared(
        config: &Config,
        prepared: Vec<PreparedMount>,
        configured_root_mount_id: Option<&str>,
    ) -> Result<Self, MountInitError> {
        let staging_quota = StagingQuota::new(
            config.limits.max_staged_file_bytes,
            config.limits.max_staged_file_objects,
        )?;
        let mut mounts = BTreeMap::new();
        for prepared in prepared {
            if mounts.contains_key(&prepared.mount_id) {
                return Err(MountInitError::new("mount_id values must be unique"));
            }
            let root = open_pinned_directory(&prepared.native_root, "mount root")?;
            let mount = Arc::new(Mount {
                mount_id: prepared.mount_id.clone(),
                native_root: prepared.native_root,
                root: Arc::new(root),
                writable: prepared.writable,
                allow_command_execution: prepared.allow_command_execution,
                max_file_bytes: prepared.max_file_bytes,
                allowed_operations: prepared.allowed_operations,
                cleanup_fault: AtomicBool::new(false),
                staging_quota: staging_quota.clone(),
            });
            mounts.insert(prepared.mount_id, mount);
        }
        let root_mount_id = match configured_root_mount_id {
            Some(mount_id) if mounts.contains_key(mount_id) => Some(mount_id.to_owned()),
            Some(_) => {
                return Err(MountInitError::new(
                    "root_mount_id must reference a configured mount",
                ));
            }
            None if mounts.len() == 1 => mounts.keys().next().cloned(),
            None if mounts.len() > 1 => {
                return Err(MountInitError::new(
                    "root_mount_id is required when multiple mounts are configured",
                ));
            }
            None => None,
        };
        Ok(Self {
            mounts,
            root_mount_id,
        })
    }

    pub(crate) fn root_mount_id(&self) -> Option<&str> {
        self.root_mount_id.as_deref()
    }

    pub(crate) fn get(&self, mount_id: &str) -> Option<Arc<Mount>> {
        self.mounts.get(mount_id).cloned()
    }

    pub(crate) fn descriptors(&self) -> Vec<MountDescriptor> {
        self.mounts
            .values()
            .map(|mount| mount.descriptor())
            .collect()
    }

    pub(crate) fn has_complete_read_family(&self) -> bool {
        self.mounts
            .values()
            .any(|mount| READ_OPERATIONS.iter().all(|name| mount.allows(name)))
    }

    pub(crate) fn has_complete_write_family(&self) -> bool {
        cfg!(any(target_os = "linux", target_os = "macos"))
            && self.mounts.values().any(|mount| {
                mount.writable && WRITE_OPERATIONS.iter().all(|name| mount.allows(name))
            })
    }

    pub(crate) fn supports_anywhere(&self, operation: &str) -> bool {
        self.mounts.values().any(|mount| mount.allows(operation))
    }

    pub(crate) fn supports_commands(&self) -> bool {
        self.mounts
            .values()
            .any(|mount| mount.allow_command_execution && mount.allows("command_cwd"))
    }

    pub(crate) fn resolve_command_cwd(&self, path: &EIPPath) -> Result<CommandCwd, MountPathError> {
        let mount = self.get(&path.mount_id).ok_or(MountPathError::Denied)?;
        if !mount.allow_command_execution || !mount.allows("command_cwd") {
            return Err(MountPathError::Denied);
        }
        let relative = mount.relative_path(path)?;
        let canonical = mount
            .root
            .canonicalize(&relative)
            .map_err(MountPathError::from_io)?;
        let metadata = mount
            .root
            .metadata(&canonical)
            .map_err(MountPathError::from_io)?;
        if !metadata.is_dir() {
            return Err(MountPathError::Denied);
        }
        validate_canonical_relative(&canonical)?;
        Ok(CommandCwd {
            native_path: mount.native_root.join(&canonical),
            mount,
            relative: canonical,
        })
    }
}

impl CommandCwd {
    pub(crate) fn resolve_relative_executable(
        &self,
        executable: &str,
    ) -> Result<PathBuf, MountPathError> {
        if !self.mount.allows("executable_source") {
            return Err(MountPathError::Denied);
        }
        let requested = Path::new(executable);
        if requested.is_absolute()
            || requested
                .components()
                .any(|component| matches!(component, Component::Prefix(_) | Component::RootDir))
        {
            return Err(MountPathError::Denied);
        }
        let candidate = self.relative.join(requested);
        let canonical = self
            .mount
            .root
            .canonicalize(candidate)
            .map_err(MountPathError::from_io)?;
        validate_canonical_relative(&canonical)?;
        let metadata = self
            .mount
            .root
            .metadata(&canonical)
            .map_err(MountPathError::from_io)?;
        if !metadata.is_file() {
            return Err(MountPathError::NotRegular);
        }
        #[cfg(unix)]
        {
            use cap_std::fs::PermissionsExt;
            if metadata.permissions().mode() & 0o111 == 0 {
                return Err(MountPathError::Denied);
            }
        }
        Ok(self.mount.native_root.join(canonical))
    }
}

fn validate_canonical_relative(path: &Path) -> Result<(), MountPathError> {
    for component in path.components() {
        let Component::Normal(segment) = component else {
            if component == Component::CurDir {
                continue;
            }
            return Err(MountPathError::Denied);
        };
        if segment.is_empty() {
            return Err(MountPathError::Denied);
        }
    }
    Ok(())
}

impl StagingQuota {
    fn new(max_bytes: u64, max_objects: u64) -> Result<Self, MountInitError> {
        if max_bytes == 0 || max_objects == 0 {
            return Err(MountInitError::new(
                "staging byte and object limits must be positive",
            ));
        }
        Ok(Self {
            inner: Arc::new(StagingQuotaInner {
                state: Mutex::new(StagingQuotaState::default()),
                max_bytes,
                max_objects,
            }),
        })
    }

    fn reserve_object(&self) -> Result<StagingReservation, MountPathError> {
        let mut state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        if state.objects >= self.inner.max_objects {
            return Err(MountPathError::Quota);
        }
        state.objects += 1;
        Ok(StagingReservation {
            quota: self.clone(),
            bytes: 0,
            state: ReservationState::Active,
        })
    }

    fn reserve_bytes(&self, bytes: u64) -> Result<(), MountPathError> {
        let mut state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        let next = state
            .bytes
            .checked_add(bytes)
            .ok_or(MountPathError::Quota)?;
        if next > self.inner.max_bytes {
            return Err(MountPathError::Quota);
        }
        state.bytes = next;
        Ok(())
    }

    fn release(&self, bytes: u64) {
        let mut state = self
            .inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner);
        state.bytes = state
            .bytes
            .checked_sub(bytes)
            .expect("staging byte reservation releases exactly once");
        state.objects = state
            .objects
            .checked_sub(1)
            .expect("staging object reservation releases exactly once");
    }
}

impl StagingReservation {
    fn reserve_bytes(&mut self, bytes: u64) -> Result<(), MountPathError> {
        if self.state != ReservationState::Active {
            return Err(MountPathError::Internal);
        }
        self.quota.reserve_bytes(bytes)?;
        self.bytes = self
            .bytes
            .checked_add(bytes)
            .ok_or(MountPathError::Internal)?;
        Ok(())
    }

    fn release(&mut self) {
        if self.state == ReservationState::Active {
            self.quota.release(self.bytes);
            self.state = ReservationState::Released;
        }
    }

    fn retain(&mut self) {
        if self.state == ReservationState::Active {
            self.state = ReservationState::Retained;
        }
    }
}

impl Drop for StagingReservation {
    fn drop(&mut self) {
        self.release();
    }
}

impl Mount {
    pub(crate) fn cleanup_faulted(&self) -> bool {
        self.cleanup_fault.load(Ordering::Acquire)
    }

    pub(crate) fn allows(&self, operation: &str) -> bool {
        self.allowed_operations.contains(operation)
    }

    pub(crate) fn descriptor(&self) -> MountDescriptor {
        MountDescriptor {
            mount_id: self.mount_id.clone(),
            logical_root: "/".to_owned(),
            writable: self.writable,
            case_sensitive: None,
            supports_atomic_replace: self.writable
                && cfg!(any(target_os = "linux", target_os = "macos")),
            max_file_bytes: self.max_file_bytes,
        }
    }

    pub(crate) fn relative_path(&self, path: &EIPPath) -> Result<PathBuf, MountPathError> {
        if path.mount_id != self.mount_id {
            return Err(MountPathError::Denied);
        }
        logical_to_relative(&path.path)
    }

    pub(crate) fn resolve_contained_target(
        &self,
        path: &EIPPath,
    ) -> Result<EIPPath, MountPathError> {
        let relative = self.relative_path(path)?;
        let canonical = self
            .root
            .canonicalize(relative)
            .map_err(MountPathError::from_io)?;
        if canonical == Path::new(".") {
            return Err(MountPathError::Denied);
        }
        let mut segments = Vec::new();
        for component in canonical.components() {
            let Component::Normal(segment) = component else {
                return Err(MountPathError::Denied);
            };
            let segment = segment.to_str().ok_or(MountPathError::Denied)?;
            if segment.is_empty() || segment.contains('/') || segment.contains('\0') {
                return Err(MountPathError::Denied);
            }
            segments.push(segment);
        }
        if segments.is_empty() {
            return Err(MountPathError::Denied);
        }
        Ok(EIPPath {
            mount_id: self.mount_id.clone(),
            path: format!("/{}", segments.join("/")),
        })
    }

    pub(crate) fn open_regular(&self, path: &EIPPath) -> Result<OpenedFile, MountPathError> {
        let relative = self.relative_path(path)?;
        let file = self
            .root
            .open(&relative)
            .map_err(MountPathError::from_io)?
            .into_std();
        let metadata = file.metadata().map_err(MountPathError::from_io)?;
        if !metadata.is_file() {
            return Err(MountPathError::NotRegular);
        }
        if metadata.len() > self.max_file_bytes {
            return Err(MountPathError::Limit);
        }
        Ok(OpenedFile { file, metadata })
    }

    pub(crate) fn metadata(
        &self,
        path: &EIPPath,
        follow_symlinks: bool,
    ) -> Result<cap_std::fs::Metadata, MountPathError> {
        let relative = self.relative_path(path)?;
        if follow_symlinks {
            self.root.metadata(relative)
        } else {
            self.root.symlink_metadata(relative)
        }
        .map_err(MountPathError::from_io)
    }

    pub(crate) fn create_dir(&self, relative: &Path) -> Result<(), MountPathError> {
        self.root
            .create_dir(relative)
            .map_err(MountPathError::from_io)
    }

    pub(crate) fn remove_file(&self, relative: &Path) -> Result<(), MountPathError> {
        self.root
            .remove_file(relative)
            .map_err(MountPathError::from_io)
    }

    pub(crate) fn remove_dir(&self, relative: &Path) -> Result<(), MountPathError> {
        self.root
            .remove_dir(relative)
            .map_err(MountPathError::from_io)
    }

    pub(crate) fn rename_within(
        &self,
        source: &EIPPath,
        destination: &EIPPath,
        replace: bool,
    ) -> Result<(), MountPathError> {
        let source = self.relative_path(source)?;
        if source == Path::new(".") {
            return Err(MountPathError::Denied);
        }
        let source_parent_path = source
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
            .unwrap_or_else(|| Path::new("."));
        let source_parent = self
            .root
            .open_dir(source_parent_path)
            .map_err(MountPathError::from_io)?;
        let (destination_parent, destination_name) = self.open_parent(destination)?;
        if replace {
            self.root
                .rename(&source, &destination_parent, &destination_name)
                .map_err(MountPathError::from_io)?;
        } else {
            rename_no_replace(
                &self.root,
                &source,
                &destination_parent,
                Path::new(&destination_name),
            )
            .map_err(MountPathError::from_io)?;
        }
        sync_directory(&destination_parent).map_err(|_| MountPathError::UnknownOutcome)?;
        sync_directory(&source_parent).map_err(|_| MountPathError::UnknownOutcome)
    }

    pub(crate) fn open_parent(
        &self,
        path: &EIPPath,
    ) -> Result<(Dir, std::ffi::OsString), MountPathError> {
        let relative = self.relative_path(path)?;
        if relative == Path::new(".") {
            return Err(MountPathError::Denied);
        }
        let name = relative
            .file_name()
            .ok_or(MountPathError::Denied)?
            .to_os_string();
        let parent = relative
            .parent()
            .filter(|parent| !parent.as_os_str().is_empty())
            .unwrap_or_else(|| Path::new("."));
        let directory = self
            .root
            .open_dir(parent)
            .map_err(MountPathError::from_io)?;
        Ok((directory, name))
    }

    pub(crate) fn create_candidate(
        self: &Arc<Self>,
        destination: &EIPPath,
    ) -> Result<StagedCandidate, MountPathError> {
        if self.cleanup_faulted() {
            return Err(MountPathError::Internal);
        }
        let (parent, destination_name) = self.open_parent(destination)?;
        let reservation = self.staging_quota.reserve_object()?;
        for _ in 0..32 {
            let name = random_candidate_name().map_err(|_| MountPathError::Internal)?;
            let mut options = cap_std::fs::OpenOptions::new();
            options.write(true).read(true).create_new(true);
            match parent.open_with(&name, &options) {
                Ok(file) => {
                    let file = file.into_std();
                    let candidate = StagedCandidate {
                        name,
                        file,
                        mount: Arc::clone(self),
                        destination: destination.clone(),
                        destination_name,
                        parent,
                        reservation,
                        removed: false,
                    };
                    set_private_file_permissions(&candidate.file)
                        .map_err(MountPathError::from_io)?;
                    return Ok(candidate);
                }
                Err(error) if error.kind() == std::io::ErrorKind::AlreadyExists => continue,
                Err(error) => return Err(MountPathError::from_io(error)),
            }
        }
        Err(MountPathError::Internal)
    }

    pub(crate) fn publish_candidate(
        &self,
        candidate: &mut StagedCandidate,
        destination: &EIPPath,
        replace: bool,
    ) -> Result<(), MountPathError> {
        if candidate.mount().mount_id != self.mount_id || candidate.destination != *destination {
            return Err(MountPathError::Denied);
        }
        let source = Path::new(&candidate.name);
        let target = Path::new(&candidate.destination_name);
        let opened = candidate.file.metadata().map_err(MountPathError::from_io)?;
        let named = candidate
            .parent
            .symlink_metadata(source)
            .map_err(MountPathError::from_io)?;
        if !named.is_file() || named.is_symlink() || !candidate_identity_matches(&opened, &named) {
            return Err(MountPathError::Denied);
        }
        if replace {
            candidate
                .parent
                .rename(source, &candidate.parent, target)
                .map_err(MountPathError::from_io)?;
        } else {
            rename_no_replace(&candidate.parent, source, &candidate.parent, target)
                .map_err(MountPathError::from_io)?;
        }
        candidate.mark_removed();
        sync_directory(&candidate.parent).map_err(|_| MountPathError::UnknownOutcome)?;
        Ok(())
    }
}

impl StagedCandidate {
    pub(crate) fn mount(&self) -> &Arc<Mount> {
        &self.mount
    }

    pub(crate) fn reserve_bytes(&mut self, bytes: u64) -> Result<(), MountPathError> {
        self.reservation.reserve_bytes(bytes)
    }

    pub(crate) fn mark_removed(&mut self) {
        self.removed = true;
        self.reservation.release();
    }

    pub(crate) fn delete(mut self) -> Result<(), MountPathError> {
        let result = self
            .parent
            .remove_file(&self.name)
            .map_err(MountPathError::from_io);
        self.removed = true;
        if result.is_ok() {
            self.reservation.release();
        } else {
            self.reservation.retain();
            self.mount.cleanup_fault.store(true, Ordering::Release);
        }
        result
    }
}

impl Drop for StagedCandidate {
    fn drop(&mut self) {
        if !self.removed {
            if self.parent.remove_file(&self.name).is_ok() {
                self.reservation.release();
            } else {
                self.reservation.retain();
                self.mount.cleanup_fault.store(true, Ordering::Release);
            }
        }
    }
}

struct PreparedMount {
    mount_id: String,
    native_root: PathBuf,
    writable: bool,
    allow_command_execution: bool,
    max_file_bytes: u64,
    allowed_operations: BTreeSet<String>,
}

impl PreparedMount {
    fn new(config: &TrustedMountConfig) -> Result<Self, MountInitError> {
        if !valid_mount_id(&config.mount_id) {
            return Err(MountInitError::new(
                "mount_id must use 1..=128 ASCII letters, digits, dot, dash, or underscore",
            ));
        }
        if config.max_file_bytes == 0 {
            return Err(MountInitError::new("mount max_file_bytes must be positive"));
        }
        let native_root = canonical_directory(&config.native_root, "native_root")?;
        let mut allowed_operations = if config.allowed_operations.is_empty() {
            let mut defaults = READ_OPERATIONS
                .iter()
                .map(|value| (*value).to_owned())
                .collect::<BTreeSet<_>>();
            defaults.extend(OPTIONAL_OPERATIONS.iter().map(|value| (*value).to_owned()));
            if config.writable {
                defaults.extend(WRITE_OPERATIONS.iter().map(|value| (*value).to_owned()));
            }
            defaults
        } else {
            config.allowed_operations.iter().cloned().collect()
        };
        let known = READ_OPERATIONS
            .iter()
            .chain(WRITE_OPERATIONS)
            .chain(OPTIONAL_OPERATIONS)
            .copied()
            .collect::<BTreeSet<_>>();
        if allowed_operations
            .iter()
            .any(|operation| !known.contains(operation.as_str()))
        {
            return Err(MountInitError::new(
                "mount allowed_operations contains an unknown operation",
            ));
        }
        if !config.writable {
            for operation in WRITE_OPERATIONS {
                allowed_operations.remove(*operation);
            }
        }
        Ok(Self {
            mount_id: config.mount_id.clone(),
            native_root,
            writable: config.writable,
            allow_command_execution: config.allow_command_execution,
            max_file_bytes: config.max_file_bytes,
            allowed_operations,
        })
    }
}

fn validate_topology(mounts: &[PreparedMount]) -> Result<(), MountInitError> {
    for (index, mount) in mounts.iter().enumerate() {
        for other in mounts.iter().skip(index + 1) {
            if overlaps(&mount.native_root, &other.native_root) {
                return Err(MountInitError::new(
                    "native mount roots must not overlap or alias",
                ));
            }
        }
    }
    Ok(())
}

fn overlaps(left: &Path, right: &Path) -> bool {
    left == right || left.starts_with(right) || right.starts_with(left)
}

fn logical_to_relative(path: &str) -> Result<PathBuf, MountPathError> {
    if path == "/" {
        return Ok(PathBuf::from("."));
    }
    if !path.starts_with('/') || path.ends_with('/') || path.contains('\0') {
        return Err(MountPathError::Invalid);
    }
    let mut relative = PathBuf::new();
    for segment in path[1..].split('/') {
        if segment.is_empty() {
            return Err(MountPathError::Invalid);
        }
        let mut components = Path::new(segment).components();
        match (components.next(), components.next()) {
            (Some(Component::Normal(value)), None) => relative.push(value),
            _ => return Err(MountPathError::Invalid),
        }
    }
    if relative.as_os_str().is_empty() {
        return Err(MountPathError::Invalid);
    }
    Ok(relative)
}

fn canonical_directory(path: &Path, field: &str) -> Result<PathBuf, MountInitError> {
    if !path.is_absolute() {
        return Err(MountInitError::new(format!(
            "mount {field} must be absolute"
        )));
    }
    let metadata = fs::symlink_metadata(path)
        .map_err(|error| MountInitError::io("inspect mount directory", error))?;
    if metadata.file_type().is_symlink() || !metadata.is_dir() {
        return Err(MountInitError::new(format!(
            "mount {field} must identify an existing non-symlink directory"
        )));
    }
    path.canonicalize()
        .map_err(|error| MountInitError::io("canonicalize mount directory", error))
}

fn open_pinned_directory(path: &Path, label: &str) -> Result<Dir, MountInitError> {
    let before = fs::metadata(path)
        .map_err(|error| MountInitError::io("inspect canonical directory", error))?;
    let directory = Dir::open_ambient_dir(path, ambient_authority())
        .map_err(|error| MountInitError::io("open pinned directory", error))?;
    let opened = directory
        .try_clone()
        .and_then(|directory| directory.into_std_file().metadata())
        .map_err(|error| MountInitError::io("inspect pinned directory", error))?;
    let after = fs::metadata(path)
        .map_err(|error| MountInitError::io("reinspect canonical directory", error))?;
    if !same_directory_identity(&before, &opened) || !same_directory_identity(&opened, &after) {
        return Err(MountInitError::new(format!(
            "{label} identity changed while it was opened"
        )));
    }
    Ok(directory)
}

#[cfg(unix)]
fn same_directory_identity(left: &fs::Metadata, right: &fs::Metadata) -> bool {
    use std::os::unix::fs::MetadataExt;
    left.is_dir() && right.is_dir() && left.dev() == right.dev() && left.ino() == right.ino()
}

#[cfg(not(unix))]
fn same_directory_identity(left: &fs::Metadata, right: &fs::Metadata) -> bool {
    left.is_dir() && right.is_dir()
}

fn random_candidate_name() -> Result<String, getrandom::Error> {
    let mut bytes = [0_u8; CANDIDATE_RANDOM_BYTES];
    getrandom::fill(&mut bytes)?;
    let mut name = String::with_capacity(CANDIDATE_PREFIX.len() + CANDIDATE_RANDOM_BYTES * 2);
    name.push_str(CANDIDATE_PREFIX);
    for byte in bytes {
        use fmt::Write as _;
        let _ = write!(name, "{byte:02x}");
    }
    Ok(name)
}

#[cfg(test)]
fn valid_candidate_name(name: &str) -> bool {
    name.len() == CANDIDATE_PREFIX.len() + CANDIDATE_RANDOM_BYTES * 2
        && name.starts_with(CANDIDATE_PREFIX)
        && name[CANDIDATE_PREFIX.len()..]
            .bytes()
            .all(|byte| byte.is_ascii_hexdigit() && !byte.is_ascii_uppercase())
}

fn valid_mount_id(value: &str) -> bool {
    !value.is_empty()
        && value.len() <= 128
        && value
            .bytes()
            .all(|byte| byte.is_ascii_alphanumeric() || matches!(byte, b'.' | b'-' | b'_'))
}

#[cfg(unix)]
fn set_private_file_permissions(file: &fs::File) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    file.set_permissions(fs::Permissions::from_mode(0o600))
}

#[cfg(not(unix))]
fn set_private_file_permissions(_file: &fs::File) -> std::io::Result<()> {
    Ok(())
}

fn sync_directory(directory: &Dir) -> std::io::Result<()> {
    directory.open(".")?.sync_all()
}

#[cfg(unix)]
fn candidate_identity_matches(opened: &std::fs::Metadata, named: &cap_std::fs::Metadata) -> bool {
    use cap_std::fs::MetadataExt as CapMetadataExt;
    use std::os::unix::fs::MetadataExt as StdMetadataExt;
    let file_type_mask = u64::from(libc::S_IFMT);
    StdMetadataExt::dev(opened) == CapMetadataExt::dev(named)
        && StdMetadataExt::ino(opened) == CapMetadataExt::ino(named)
        && (u64::from(StdMetadataExt::mode(opened)) & file_type_mask)
            == (u64::from(CapMetadataExt::mode(named)) & file_type_mask)
}

#[cfg(not(unix))]
fn candidate_identity_matches(_opened: &std::fs::Metadata, _named: &cap_std::fs::Metadata) -> bool {
    false
}

#[cfg(target_os = "macos")]
fn rename_no_replace(
    source_dir: &Dir,
    source: &Path,
    destination_dir: &Dir,
    destination: &Path,
) -> std::io::Result<()> {
    let source = CString::new(source.as_os_str().as_bytes()).map_err(|_| {
        std::io::Error::new(std::io::ErrorKind::InvalidInput, "source contains NUL")
    })?;
    let destination = CString::new(destination.as_os_str().as_bytes()).map_err(|_| {
        std::io::Error::new(std::io::ErrorKind::InvalidInput, "destination contains NUL")
    })?;
    let result = unsafe {
        libc::renameatx_np(
            source_dir.as_raw_fd(),
            source.as_ptr(),
            destination_dir.as_raw_fd(),
            destination.as_ptr(),
            libc::RENAME_EXCL,
        )
    };
    if result == 0 {
        Ok(())
    } else {
        Err(std::io::Error::last_os_error())
    }
}

#[cfg(target_os = "linux")]
fn rename_no_replace(
    source_dir: &Dir,
    source: &Path,
    destination_dir: &Dir,
    destination: &Path,
) -> std::io::Result<()> {
    let source = CString::new(source.as_os_str().as_bytes()).map_err(|_| {
        std::io::Error::new(std::io::ErrorKind::InvalidInput, "source contains NUL")
    })?;
    let destination = CString::new(destination.as_os_str().as_bytes()).map_err(|_| {
        std::io::Error::new(std::io::ErrorKind::InvalidInput, "destination contains NUL")
    })?;
    let result = unsafe {
        libc::syscall(
            libc::SYS_renameat2,
            source_dir.as_raw_fd(),
            source.as_ptr(),
            destination_dir.as_raw_fd(),
            destination.as_ptr(),
            libc::RENAME_NOREPLACE,
        )
    };
    if result == 0 {
        Ok(())
    } else {
        Err(std::io::Error::last_os_error())
    }
}

#[cfg(not(any(target_os = "macos", target_os = "linux")))]
fn rename_no_replace(
    _source_dir: &Dir,
    _source: &Path,
    _destination_dir: &Dir,
    _destination: &Path,
) -> std::io::Result<()> {
    Err(std::io::Error::new(
        std::io::ErrorKind::Unsupported,
        "atomic no-replace rename is unsupported on this platform",
    ))
}

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
pub(crate) enum MountPathError {
    Invalid,
    Denied,
    NotFound,
    AlreadyExists,
    NotRegular,
    Limit,
    Quota,
    Unsupported,
    UnknownOutcome,
    Io,
    Internal,
}

impl MountPathError {
    fn from_io(error: std::io::Error) -> Self {
        match error.kind() {
            std::io::ErrorKind::NotFound => Self::NotFound,
            std::io::ErrorKind::AlreadyExists => Self::AlreadyExists,
            std::io::ErrorKind::PermissionDenied => Self::Denied,
            std::io::ErrorKind::Unsupported => Self::Unsupported,
            _ => Self::Io,
        }
    }
}

#[derive(Debug)]
pub(crate) struct MountInitError {
    message: String,
}

impl MountInitError {
    fn new(message: impl Into<String>) -> Self {
        Self {
            message: message.into(),
        }
    }

    fn io(action: &str, error: std::io::Error) -> Self {
        Self::new(format!("{action}: {error}"))
    }
}

impl fmt::Display for MountInitError {
    fn fmt(&self, formatter: &mut fmt::Formatter<'_>) -> fmt::Result {
        formatter.write_str(&self.message)
    }
}

impl Error for MountInitError {}

#[cfg(test)]
mod tests {
    use super::{logical_to_relative, valid_candidate_name, valid_mount_id};

    #[test]
    fn validates_logical_paths_without_normalizing_authority() {
        assert_eq!(
            logical_to_relative("/").expect("root is valid"),
            std::path::Path::new(".")
        );
        assert_eq!(
            logical_to_relative("/one/two").expect("path is valid"),
            std::path::Path::new("one/two")
        );
        for invalid in [
            "",
            "relative",
            "/one/",
            "/one//two",
            "/one/../two",
            "/one/./two",
        ] {
            assert!(logical_to_relative(invalid).is_err(), "{invalid}");
        }
    }

    #[test]
    fn candidate_and_mount_names_are_bounded() {
        assert!(valid_mount_id("workspace-1"));
        assert!(!valid_mount_id(""));
        assert!(!valid_mount_id("bad/name"));
        assert!(valid_candidate_name(
            ".eip-stage-0123456789abcdef0123456789abcdef"
        ));
        assert!(!valid_candidate_name(".eip-stage-../escape"));
    }
}
