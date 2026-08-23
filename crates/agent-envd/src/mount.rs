use std::{
    collections::{BTreeMap, BTreeSet},
    error::Error,
    fmt, fs,
    path::{Component, Path, PathBuf},
    sync::{
        Arc, Mutex, MutexGuard, PoisonError,
        atomic::{AtomicBool, Ordering},
    },
    time::{Duration, Instant},
};

#[cfg(any(target_os = "linux", target_os = "macos"))]
use std::ffi::CString;
#[cfg(unix)]
use std::os::unix::{ffi::OsStrExt, io::AsRawFd};

use cap_std::{ambient_authority, fs::Dir};

use crate::{
    config::{Config, TrustedMountConfig},
    eip::{EIPPath, MountDescriptor},
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
const CANDIDATE_PREFIX: &str = "eip-stage-";
const CANDIDATE_RANDOM_BYTES: usize = 16;

#[derive(Clone)]
pub(crate) struct MountRegistry {
    mounts: BTreeMap<String, Arc<Mount>>,
}

pub(crate) struct Mount {
    pub(crate) mount_id: String,
    pub(crate) native_root: PathBuf,
    pub(crate) root: Arc<Dir>,
    #[allow(dead_code)] // Retained for diagnostics without exposing it through EIP.
    pub(crate) staging_root: Option<PathBuf>,
    pub(crate) staging: Option<Arc<Dir>>,
    pub(crate) writable: bool,
    exclusive_mutation_control: bool,
    pub(crate) allow_command_execution: bool,
    pub(crate) max_file_bytes: u64,
    allowed_operations: BTreeSet<String>,
    mutation_gate: Mutex<()>,
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
    pub(crate) fn initialize(config: &Config) -> Result<Self, MountInitError> {
        let staging_quota = StagingQuota::new(
            config.limits.max_staged_file_bytes,
            config.limits.max_staged_file_objects,
        )?;
        let mut prepared = Vec::with_capacity(config.mounts.len());
        for configured in &config.mounts {
            prepared.push(PreparedMount::new(configured)?);
        }
        validate_topology(&prepared)?;

        let mut mounts = BTreeMap::new();
        for prepared in prepared {
            if mounts.contains_key(&prepared.mount_id) {
                return Err(MountInitError::new("mount_id values must be unique"));
            }
            if let Some(staging_root) = &prepared.staging_root {
                scavenge_staging_root(
                    staging_root,
                    config.limits.max_staged_file_objects,
                    config.limits.max_staged_file_bytes,
                    config.staging_scavenge_timeout,
                )?;
            }
            let root = open_pinned_directory(&prepared.native_root, "mount root")?;
            let staging = prepared
                .staging_root
                .as_ref()
                .map(|path| open_pinned_directory(path, "staging root").map(Arc::new))
                .transpose()?;
            let mount = Arc::new(Mount {
                mount_id: prepared.mount_id.clone(),
                native_root: prepared.native_root,
                root: Arc::new(root),
                staging_root: prepared.staging_root,
                staging,
                writable: prepared.writable,
                exclusive_mutation_control: prepared.exclusive_mutation_control,
                allow_command_execution: prepared.allow_command_execution,
                max_file_bytes: prepared.max_file_bytes,
                allowed_operations: prepared.allowed_operations,
                mutation_gate: Mutex::new(()),
                cleanup_fault: AtomicBool::new(false),
                staging_quota: staging_quota.clone(),
            });
            mounts.insert(prepared.mount_id, mount);
        }
        Ok(Self { mounts })
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
                mount.writable
                    && mount.exclusive_mutation_control
                    && WRITE_OPERATIONS.iter().all(|name| mount.allows(name))
                    && mount.staging.is_some()
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
    pub(crate) fn mutation_guard(&self) -> MutexGuard<'_, ()> {
        self.mutation_gate
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
    }

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
            writable: self.writable && self.exclusive_mutation_control,
            case_sensitive: Some(cfg!(not(target_os = "windows"))),
            supports_atomic_replace: self.writable
                && self.exclusive_mutation_control
                && self.staging.is_some()
                && cfg!(any(target_os = "linux", target_os = "macos")),
            supports_file_revision: cfg!(unix),
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

    pub(crate) fn create_candidate(self: &Arc<Self>) -> Result<StagedCandidate, MountPathError> {
        if self.cleanup_faulted() {
            return Err(MountPathError::Internal);
        }
        let staging = self.staging.as_ref().ok_or(MountPathError::Unsupported)?;
        let reservation = self.staging_quota.reserve_object()?;
        for _ in 0..32 {
            let name = random_candidate_name().map_err(|_| MountPathError::Internal)?;
            let mut options = cap_std::fs::OpenOptions::new();
            options.write(true).read(true).create_new(true);
            match staging.open_with(&name, &options) {
                Ok(file) => {
                    let file = file.into_std();
                    let candidate = StagedCandidate {
                        name,
                        file,
                        mount: Arc::clone(self),
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

    pub(crate) fn remove_candidate(&self, name: &str) -> Result<(), MountPathError> {
        if !valid_candidate_name(name) {
            return Err(MountPathError::Denied);
        }
        self.staging
            .as_ref()
            .ok_or(MountPathError::Unsupported)?
            .remove_file(name)
            .map_err(MountPathError::from_io)
    }

    pub(crate) fn staging_dir(&self) -> Result<&Dir, MountPathError> {
        self.staging.as_deref().ok_or(MountPathError::Unsupported)
    }

    pub(crate) fn publish_candidate(
        &self,
        candidate: &mut StagedCandidate,
        destination: &EIPPath,
        replace: bool,
    ) -> Result<(), MountPathError> {
        if candidate.mount().mount_id != self.mount_id {
            return Err(MountPathError::Denied);
        }
        let (parent, name) = self.open_parent(destination)?;
        let staging = self.staging_dir()?;
        let source = Path::new(&candidate.name);
        let target = Path::new(&name);
        let opened = candidate.file.metadata().map_err(MountPathError::from_io)?;
        let named = staging
            .symlink_metadata(source)
            .map_err(MountPathError::from_io)?;
        if !named.is_file() || named.is_symlink() || !candidate_identity_matches(&opened, &named) {
            return Err(MountPathError::Denied);
        }
        if replace {
            staging
                .rename(source, &parent, target)
                .map_err(MountPathError::from_io)?;
        } else {
            rename_no_replace(staging, source, &parent, target).map_err(MountPathError::from_io)?;
        }
        candidate.mark_removed();
        sync_directory(&parent).map_err(|_| MountPathError::UnknownOutcome)?;
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
        let result = self.mount.remove_candidate(&self.name);
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
            if self.mount.remove_candidate(&self.name).is_ok() {
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
    staging_root: Option<PathBuf>,
    writable: bool,
    exclusive_mutation_control: bool,
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
        if config.writable && !config.exclusive_mutation_control {
            return Err(MountInitError::new(
                "a writable mount requires exclusive_mutation_control",
            ));
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
        let needs_staging = WRITE_OPERATIONS
            .iter()
            .any(|operation| allowed_operations.contains(*operation));
        let staging_root = config
            .staging_root
            .as_ref()
            .map(|path| canonical_directory(path, "staging_root"))
            .transpose()?;
        if needs_staging && staging_root.is_none() {
            return Err(MountInitError::new(
                "a writable mount requires a private staging_root",
            ));
        }
        if let Some(staging_root) = &staging_root {
            validate_private_directory(staging_root)?;
            if !same_filesystem(&native_root, staging_root)? {
                return Err(MountInitError::new(
                    "mount native_root and staging_root must be on the same filesystem",
                ));
            }
        }
        Ok(Self {
            mount_id: config.mount_id.clone(),
            native_root,
            staging_root,
            writable: config.writable,
            exclusive_mutation_control: config.exclusive_mutation_control,
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
    for mount in mounts {
        if let Some(staging) = &mount.staging_root {
            for visible in mounts {
                if overlaps(staging, &visible.native_root) {
                    return Err(MountInitError::new(
                        "staging roots must be outside every logical mount",
                    ));
                }
            }
            for other in mounts {
                if let Some(other_staging) = &other.staging_root
                    && staging != other_staging
                    && overlaps(staging, other_staging)
                {
                    return Err(MountInitError::new("staging roots must not overlap"));
                }
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

fn validate_private_directory(path: &Path) -> Result<(), MountInitError> {
    #[cfg(not(unix))]
    let _ = path;
    #[cfg(unix)]
    {
        use std::os::unix::fs::{MetadataExt, PermissionsExt};
        let metadata = fs::metadata(path)
            .map_err(|error| MountInitError::io("inspect staging root", error))?;
        if metadata.permissions().mode() & 0o077 != 0 {
            return Err(MountInitError::new(
                "staging_root must not grant group or other permissions",
            ));
        }
        if metadata.uid() != unsafe { libc::geteuid() } {
            return Err(MountInitError::new(
                "staging_root must be owned by the daemon effective user",
            ));
        }
    }
    Ok(())
}

fn same_filesystem(left: &Path, right: &Path) -> Result<bool, MountInitError> {
    #[cfg(unix)]
    {
        use std::os::unix::fs::MetadataExt;
        let left = fs::metadata(left)
            .map_err(|error| MountInitError::io("inspect native_root filesystem", error))?;
        let right = fs::metadata(right)
            .map_err(|error| MountInitError::io("inspect staging_root filesystem", error))?;
        Ok(left.dev() == right.dev())
    }
    #[cfg(not(unix))]
    {
        let _ = (left, right);
        Ok(false)
    }
}

fn scavenge_staging_root(
    root: &Path,
    max_objects: u64,
    max_bytes: u64,
    timeout: Duration,
) -> Result<(), MountInitError> {
    let started = Instant::now();
    let mut objects = 0_u64;
    let mut bytes = 0_u64;
    let entries =
        fs::read_dir(root).map_err(|error| MountInitError::io("read staging root", error))?;
    for entry in entries {
        if started.elapsed() >= timeout {
            return Err(MountInitError::new(
                "staging root scavenging exceeded its startup timeout",
            ));
        }
        let entry = entry.map_err(|error| MountInitError::io("read staging entry", error))?;
        let name = entry
            .file_name()
            .to_str()
            .filter(|name| valid_candidate_name(name))
            .map(str::to_owned)
            .ok_or_else(|| MountInitError::new("staging root contains an unknown entry"))?;
        let metadata = fs::symlink_metadata(entry.path())
            .map_err(|error| MountInitError::io("inspect staging entry", error))?;
        if !metadata.is_file()
            || metadata.file_type().is_symlink()
            || file_has_multiple_links(&metadata)
        {
            return Err(MountInitError::new(
                "staging root entries must be single-link regular candidate files",
            ));
        }
        objects = objects
            .checked_add(1)
            .ok_or_else(|| MountInitError::new("staging object accounting overflow"))?;
        bytes = bytes
            .checked_add(metadata.len())
            .ok_or_else(|| MountInitError::new("staging byte accounting overflow"))?;
        if objects > max_objects || bytes > max_bytes {
            return Err(MountInitError::new(
                "stale staging candidates exceed configured startup limits",
            ));
        }
        fs::remove_file(root.join(name))
            .map_err(|error| MountInitError::io("remove stale staging candidate", error))?;
    }
    Ok(())
}

#[cfg(unix)]
fn file_has_multiple_links(metadata: &fs::Metadata) -> bool {
    use std::os::unix::fs::MetadataExt;
    metadata.nlink() != 1
}

#[cfg(not(unix))]
fn file_has_multiple_links(_metadata: &fs::Metadata) -> bool {
    false
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
    use crate::config::TrustedMountConfig;

    use super::{PreparedMount, logical_to_relative, valid_candidate_name, valid_mount_id};

    #[test]
    fn writable_mount_requires_exclusive_mutation_control() {
        let config = TrustedMountConfig {
            mount_id: "workspace".to_owned(),
            native_root: std::path::PathBuf::from("/not-opened"),
            staging_root: None,
            writable: true,
            exclusive_mutation_control: false,
            allow_command_execution: false,
            max_file_bytes: 1024,
            allowed_operations: Vec::new(),
        };
        let error = match PreparedMount::new(&config) {
            Err(error) => error,
            Ok(_) => panic!("unsafe writable mount must be rejected"),
        };
        assert!(error.to_string().contains("exclusive_mutation_control"));
    }

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
            "eip-stage-0123456789abcdef0123456789abcdef"
        ));
        assert!(!valid_candidate_name("eip-stage-../escape"));
    }
}
