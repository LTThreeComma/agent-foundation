use std::{
    collections::{BTreeMap, BTreeSet, VecDeque},
    io::{BufRead, BufReader, Read, Seek, SeekFrom, Write},
    path::{Path, PathBuf},
    sync::{Arc, Mutex, PoisonError},
    time::{Duration, Instant},
};

use globset::{GlobBuilder, GlobSet, GlobSetBuilder};
use regex::Regex;
use serde::Serialize;
use sha2::{Digest, Sha256};

use crate::{
    eip::{
        EIPPath, FileCopyParams, FileCopySourceStability, FileFindParams, FileFindResult, FileInfo,
        FileKind, FileListEntry, FileListParams, FileListResult, FileMkdirParams, FileMoveParams,
        FilePatchTextParams, FileReadTextParams, FileReadTextResult, FileRemoveParams,
        FileRevision, FileSearchMatch, FileSearchParams, FileSearchResult, FileStatParams,
        FileStatResult, FileTextCursor, FileWriteMode, FileWriteTextParams, FindMode, OutputCursor,
        OutputOverflow, OutputPolicy, SearchMode, StructuredOutputDisposition, TextPosition,
    },
    mount::{Mount, MountPathError, MountRegistry, StagedCandidate},
    operation::{OperationInterruption, OperationRegistry, ShortIdAllocator},
    retention::RetentionQuota,
    transfer::{file_info, file_revision},
};

const MAX_TRAVERSAL_ENTRIES: usize = 10_000;
const MAX_TRAVERSAL_DEPTH: u32 = 128;
const MAX_PATTERN_BYTES: usize = 16 * 1024;
const MAX_GLOBS: usize = 128;
const MAX_SEARCH_LINE_BYTES: usize = 64 * 1024;
const MAX_PATCH_LINE_BYTES: usize = 64 * 1024;
const MAX_PATCH_HUNKS: u64 = 10_000;
const RESPONSE_RESERVE_BYTES: u64 = 4096;

#[derive(Clone)]
pub(crate) struct ResourceRegistry {
    inner: Arc<ResourceInner>,
}

struct ResourceInner {
    state: Mutex<ResourceState>,
    quota: RetentionQuota,
    ttl: Duration,
    max_inline_bytes: u64,
    max_output_bytes: u64,
    max_response_bytes: u64,
    operations: OperationRegistry,
    selector_ids: ShortIdAllocator,
}

#[derive(Default)]
struct ResourceState {
    snapshots: BTreeMap<String, SnapshotRecord>,
    cursors: BTreeMap<String, StructuredCursorRecord>,
    text_cursors: BTreeMap<String, TextCursorRecord>,
    released_cursors: BTreeSet<String>,
    released_order: VecDeque<String>,
}

struct SnapshotRecord {
    payload: SnapshotPayload,
    charged_bytes: u64,
    expires_at: Instant,
    expires_at_utc: chrono::DateTime<chrono::Utc>,
    cursors: usize,
    dropped_items: u64,
}

#[derive(Clone)]
enum SnapshotPayload {
    Entries(Arc<Vec<FileListEntry>>),
    Matches(Arc<Vec<FileSearchMatch>>),
}

struct StructuredCursorRecord {
    snapshot_id: String,
    shape: String,
    offset: usize,
    expires_at: Instant,
}

struct StructuredSnapshot<T> {
    snapshot_id: String,
    offset: usize,
    items: Arc<Vec<T>>,
    dropped_items: u64,
}

struct TextCursorRecord {
    path: EIPPath,
    revision: FileRevision,
    offset: u64,
    line: u64,
    byte_column: u64,
    expires_at: Instant,
}

#[derive(Clone)]
struct RemovePlanEntry {
    relative: PathBuf,
    directory: bool,
    identity: CapEntryIdentity,
}

#[cfg(unix)]
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
struct CapEntryIdentity {
    device: u64,
    inode: u64,
    file_type: u64,
}

#[cfg(not(unix))]
#[derive(Clone, Copy, PartialEq, Eq, PartialOrd, Ord)]
struct CapEntryIdentity;

#[derive(Debug, PartialEq, Eq)]
pub(crate) enum ResourceError {
    Invalid,
    Denied,
    NotFound,
    Conflict,
    Unsupported,
    Limit,
    OutputLimit,
    InvalidHandle,
    Busy,
    Cancelled,
    Timeout,
    UnknownOutcome,
    Io,
    Internal,
    PartialRemove {
        removed_entries: u64,
        cause: Box<ResourceError>,
    },
}

impl ResourceRegistry {
    pub(crate) fn new(
        config: &crate::config::Config,
        operations: OperationRegistry,
        quota: RetentionQuota,
    ) -> Result<Self, ResourceError> {
        Ok(Self {
            inner: Arc::new(ResourceInner {
                state: Mutex::new(ResourceState::default()),
                quota,
                ttl: Duration::from_millis(config.limits.max_retention_ttl_ms),
                max_inline_bytes: config.limits.max_inline_output_bytes,
                max_output_bytes: config.limits.max_output_bytes,
                max_response_bytes: config.limits.max_response_bytes,
                selector_ids: ShortIdAllocator::for_generation(operations.generation()),
                operations,
            }),
        })
    }

    pub(crate) fn stat(
        &self,
        mounts: &MountRegistry,
        params: &FileStatParams,
    ) -> Result<FileStatResult, ResourceError> {
        self.check_cancelled(&params.context.operation_id)?;
        let mount = read_mount(mounts, &params.path, "stat")?;
        let metadata = mount
            .metadata(&params.path, params.follow_symlinks)
            .map_err(map_mount_error)?;
        Ok(FileStatResult {
            info: cap_file_info(&params.path, &metadata),
        })
    }

    pub(crate) fn read_text(
        &self,
        mounts: &MountRegistry,
        params: &FileReadTextParams,
    ) -> Result<FileReadTextResult, ResourceError> {
        if params.cursor.is_some() && params.start_line.is_some() {
            return Err(ResourceError::Invalid);
        }
        self.check_cancelled(&params.context.operation_id)?;
        let mount = read_mount(mounts, &params.path, "read_text")?;
        let opened = mount.open_regular(&params.path).map_err(map_mount_error)?;
        let info = file_info(&params.path, &opened.metadata);
        let revision = info.revision.clone();
        if let Some(expected) = &params.expected_revision {
            let current = revision.as_ref().ok_or(ResourceError::Unsupported)?;
            if expected != current {
                return Err(ResourceError::Conflict);
            }
        }

        let (offset, start_line, start_column) = if let Some(cursor) = &params.cursor {
            let current = revision.as_ref().ok_or(ResourceError::Unsupported)?;
            self.consume_text_cursor(cursor, &params.path, current)?
        } else {
            let line = params.start_line.unwrap_or(1);
            let offset = find_line_offset(
                &opened.file,
                line,
                opened.metadata.len(),
                &self.inner.operations,
                &params.context.operation_id,
            )?;
            (offset, line, 0)
        };
        let max_bytes = params
            .max_bytes
            .unwrap_or(self.inner.max_inline_bytes)
            .min(self.inner.max_inline_bytes)
            .min(
                self.inner
                    .max_response_bytes
                    .saturating_sub(RESPONSE_RESERVE_BYTES),
            )
            .max(1);
        let max_lines = params.max_lines.unwrap_or(u64::MAX);
        let page = read_text_page(
            opened.file,
            offset,
            start_line,
            start_column,
            max_bytes,
            max_lines,
            opened.metadata.len(),
        )?;
        let next_cursor = if page.complete {
            None
        } else if let Some(revision) = revision {
            Some(self.insert_text_cursor(TextCursorRecord {
                path: params.path.clone(),
                revision,
                offset: page.end_offset,
                line: page.end.line,
                byte_column: page.end.byte_column,
                expires_at: Instant::now() + self.inner.ttl,
            })?)
        } else {
            None
        };
        Ok(FileReadTextResult {
            info,
            text: page.text,
            start: TextPosition {
                line: start_line,
                byte_column: start_column,
            },
            end: page.end,
            next_cursor,
            content_complete: page.complete,
            truncated: !page.complete,
        })
    }

    pub(crate) fn list(
        &self,
        mounts: &MountRegistry,
        params: &FileListParams,
    ) -> Result<FileListResult, ResourceError> {
        let shape = shape_key("file.list", params)?;
        let page_limit = self.page_limit(params.output_policy.as_ref())?;
        if let Some(cursor) = &params.cursor {
            let snapshot = self.entries_cursor(cursor, &shape)?;
            let (items, next, encoded) = page_slice(&snapshot.items, snapshot.offset, page_limit)?;
            let snapshot_id = snapshot.snapshot_id;
            let entries = snapshot.items;
            let dropped_items = snapshot.dropped_items;
            let next_cursor =
                self.next_structured_cursor(&snapshot_id, &shape, next, entries.len())?;
            let dropped_items = dropped_items
                + if next < entries.len() && next_cursor.is_none() {
                    (entries.len() - next) as u64
                } else {
                    0
                };
            let emitted = items.len() as u64;
            let expires_at = self.cursor_expiry(next_cursor.as_ref());
            return Ok(FileListResult {
                entries: items,
                output: disposition(
                    emitted,
                    encoded,
                    next_cursor,
                    expires_at,
                    next == entries.len() && dropped_items == 0,
                    dropped_items,
                ),
            });
        }
        if params.max_depth > MAX_TRAVERSAL_DEPTH {
            return Err(ResourceError::Limit);
        }
        let mount = read_mount(mounts, &params.path, "list")?;
        let metadata = mount
            .metadata(&params.path, true)
            .map_err(map_mount_error)?;
        if !metadata.is_dir() {
            return Err(ResourceError::Denied);
        }
        let depth = if params.recursive {
            params.max_depth
        } else {
            1
        };
        let entries = walk_entries(
            &mount,
            &params.path,
            depth,
            true,
            &self.inner.operations,
            &params.context.operation_id,
        )?;
        self.finish_entries(shape, entries, page_limit, params.output_policy.as_ref())
    }

    pub(crate) fn find(
        &self,
        mounts: &MountRegistry,
        params: &FileFindParams,
    ) -> Result<FileFindResult, ResourceError> {
        if params.pattern.len() > MAX_PATTERN_BYTES || params.max_depth > MAX_TRAVERSAL_DEPTH {
            return Err(ResourceError::Limit);
        }
        let shape = shape_key("file.find", params)?;
        let page_limit = self.page_limit(params.output_policy.as_ref())?;
        if let Some(cursor) = &params.cursor {
            let snapshot = self.entries_cursor(cursor, &shape)?;
            let (items, next, encoded) = page_slice(&snapshot.items, snapshot.offset, page_limit)?;
            let snapshot_id = snapshot.snapshot_id;
            let entries = snapshot.items;
            let dropped_items = snapshot.dropped_items;
            let next_cursor =
                self.next_structured_cursor(&snapshot_id, &shape, next, entries.len())?;
            let dropped_items = dropped_items
                + if next < entries.len() && next_cursor.is_none() {
                    (entries.len() - next) as u64
                } else {
                    0
                };
            let emitted = items.len() as u64;
            let expires_at = self.cursor_expiry(next_cursor.as_ref());
            return Ok(FileFindResult {
                entries: items,
                output: disposition(
                    emitted,
                    encoded,
                    next_cursor,
                    expires_at,
                    next == entries.len() && dropped_items == 0,
                    dropped_items,
                ),
            });
        }
        let matcher = PathMatcher::new(params.mode, &params.pattern)?;
        let mount = read_mount(mounts, &params.root, "find")?;
        let entries = walk_entries(
            &mount,
            &params.root,
            params.max_depth,
            true,
            &self.inner.operations,
            &params.context.operation_id,
        )?
        .into_iter()
        .filter(|entry| matcher.matches(&entry.relative_path))
        .filter(|entry| params.kind.is_none_or(|kind| entry.info.kind == kind))
        .collect();
        let result =
            self.finish_entries(shape, entries, page_limit, params.output_policy.as_ref())?;
        Ok(FileFindResult {
            entries: result.entries,
            output: result.output,
        })
    }

    pub(crate) fn search(
        &self,
        mounts: &MountRegistry,
        params: &FileSearchParams,
    ) -> Result<FileSearchResult, ResourceError> {
        if params.query.is_empty()
            || params.query.len() > MAX_PATTERN_BYTES
            || params.include.len() + params.exclude.len() > MAX_GLOBS
            || params.max_depth > MAX_TRAVERSAL_DEPTH
        {
            return Err(ResourceError::Invalid);
        }
        let shape = shape_key("file.search", params)?;
        let page_limit = self.page_limit(params.output_policy.as_ref())?;
        if let Some(cursor) = &params.cursor {
            let snapshot = self.matches_cursor(cursor, &shape)?;
            let (items, next, encoded) = page_slice(&snapshot.items, snapshot.offset, page_limit)?;
            let snapshot_id = snapshot.snapshot_id;
            let matches = snapshot.items;
            let dropped_items = snapshot.dropped_items;
            let next_cursor =
                self.next_structured_cursor(&snapshot_id, &shape, next, matches.len())?;
            let dropped_items = dropped_items
                + if next < matches.len() && next_cursor.is_none() {
                    (matches.len() - next) as u64
                } else {
                    0
                };
            let emitted = items.len() as u64;
            let expires_at = self.cursor_expiry(next_cursor.as_ref());
            return Ok(FileSearchResult {
                matches: items,
                output: disposition(
                    emitted,
                    encoded,
                    next_cursor,
                    expires_at,
                    next == matches.len() && dropped_items == 0,
                    dropped_items,
                ),
            });
        }
        let includes = compile_globs(&params.include)?;
        let excludes = compile_globs(&params.exclude)?;
        let content = ContentMatcher::new(params.mode, &params.query, params.case_sensitive)?;
        let mount = read_mount(mounts, &params.root, "search")?;
        let entries = walk_entries(
            &mount,
            &params.root,
            params.max_depth,
            true,
            &self.inner.operations,
            &params.context.operation_id,
        )?;
        let mut matches = Vec::new();
        for entry in entries
            .into_iter()
            .filter(|entry| entry.info.kind == FileKind::File)
        {
            if includes
                .as_ref()
                .is_some_and(|set| !set.is_match(&entry.relative_path))
                || excludes
                    .as_ref()
                    .is_some_and(|set| set.is_match(&entry.relative_path))
            {
                continue;
            }
            self.check_cancelled(&params.context.operation_id)?;
            let file_matches = search_file(
                &mount,
                &entry.info.path,
                &content,
                &self.inner.operations,
                &params.context.operation_id,
            )?;
            if matches.len().saturating_add(file_matches.len()) > MAX_TRAVERSAL_ENTRIES {
                return Err(ResourceError::Limit);
            }
            matches.extend(file_matches);
        }
        self.finish_matches(shape, matches, page_limit, params.output_policy.as_ref())
    }

    pub(crate) fn write_text(
        &self,
        mounts: &MountRegistry,
        params: &FileWriteTextParams,
    ) -> Result<(FileInfo, u64), ResourceError> {
        if params.text.contains('\0') {
            return Err(ResourceError::Unsupported);
        }
        let mount = write_mount(mounts, &params.path, "write_text")?;
        let input = params.text.as_bytes();
        let current = observe_regular(&mount, &params.path)?;
        validate_write_mode(
            params.mode,
            current.as_ref(),
            params.expected_revision.as_ref(),
        )?;
        let final_size = if params.mode == FileWriteMode::Append {
            current.as_ref().map_or(0, |(_, metadata)| metadata.len())
        } else {
            0
        }
        .checked_add(input.len() as u64)
        .ok_or(ResourceError::Limit)?;
        if final_size > mount.max_file_bytes {
            return Err(ResourceError::Limit);
        }
        self.check_cancelled(&params.context.operation_id)?;
        let mut candidate = mount.create_candidate().map_err(map_mount_error)?;
        candidate
            .reserve_bytes(final_size)
            .map_err(map_mount_error)?;
        let mut intended = Sha256::new();
        if params.mode == FileWriteMode::Append {
            let source = mount
                .open_regular(&params.path)
                .map_err(map_mount_error)?
                .file;
            let prefix = read_file_bounded(
                source,
                mount.max_file_bytes,
                &self.inner.operations,
                &params.context.operation_id,
            )?;
            if std::str::from_utf8(&prefix).is_err() || prefix.contains(&0) {
                return Err(ResourceError::Unsupported);
            }
            intended.update(&prefix);
            candidate
                .file
                .write_all(&prefix)
                .map_err(|_| ResourceError::Io)?;
        }
        intended.update(input);
        candidate
            .file
            .write_all(input)
            .map_err(|_| ResourceError::Io)?;
        if let Some(executable) = params.executable {
            set_executable(&candidate.file, executable).map_err(|_| ResourceError::Unsupported)?;
        } else if let Some((_, metadata)) = &current {
            set_permissions_from(&candidate.file, metadata).map_err(|_| ResourceError::Io)?;
        }
        self.check_cancelled(&params.context.operation_id)?;
        let info = commit_candidate(
            &mount,
            &params.path,
            params.mode,
            current.as_ref().map(|(revision, _)| revision),
            &mut candidate,
            final_size,
            &format!("{:x}", intended.finalize()),
        )?;
        Ok((info, input.len() as u64))
    }

    pub(crate) fn mkdir(
        &self,
        mounts: &MountRegistry,
        params: &FileMkdirParams,
    ) -> Result<(FileInfo, u64), ResourceError> {
        let mount = write_mount(mounts, &params.path, "mkdir")?;
        let _mutation = mount.mutation_guard();
        let relative = mount.relative_path(&params.path).map_err(map_mount_error)?;
        if relative == Path::new(".") {
            return if params.exist_ok {
                let metadata = mount
                    .metadata(&params.path, false)
                    .map_err(map_mount_error)?;
                Ok((cap_file_info(&params.path, &metadata), 0))
            } else {
                Err(ResourceError::Conflict)
            };
        }
        let mut prefix = PathBuf::new();
        let mut created = 0_u64;
        let components = relative.components().collect::<Vec<_>>();
        for (index, component) in components.iter().enumerate() {
            prefix.push(component.as_os_str());
            match mount.root.symlink_metadata(&prefix) {
                Ok(metadata) => {
                    if !metadata.is_dir() || metadata.file_type().is_symlink() {
                        return Err(ResourceError::Conflict);
                    }
                    if index + 1 == components.len() && !params.exist_ok {
                        return Err(ResourceError::Conflict);
                    }
                }
                Err(error) if error.kind() == std::io::ErrorKind::NotFound => {
                    if !params.parents && index + 1 != components.len() {
                        return Err(ResourceError::NotFound);
                    }
                    mount.create_dir(&prefix).map_err(map_mount_error)?;
                    created += 1;
                }
                Err(_) => return Err(ResourceError::Io),
            }
        }
        let metadata = mount
            .metadata(&params.path, false)
            .map_err(map_mount_error)?;
        Ok((cap_file_info(&params.path, &metadata), created))
    }

    pub(crate) fn patch_text(
        &self,
        mounts: &MountRegistry,
        params: &FilePatchTextParams,
    ) -> Result<(FileInfo, u64), ResourceError> {
        if params.patch_format != "unified_diff" || params.patch.contains('\0') {
            return Err(ResourceError::Invalid);
        }
        let mount = write_mount(mounts, &params.path, "patch_text")?;
        if params.patch.len() as u64 > mount.max_file_bytes {
            return Err(ResourceError::Limit);
        }
        let _mutation = mount.mutation_guard();
        let target = mount
            .resolve_contained_target(&params.path)
            .map_err(map_mount_error)?;
        let opened = mount.open_regular(&target).map_err(map_mount_error)?;
        let current = file_revision(&opened.metadata);
        if current != params.expected_revision {
            return Err(ResourceError::Conflict);
        }
        let bytes = read_file_bounded(
            opened.file,
            mount.max_file_bytes,
            &self.inner.operations,
            &params.context.operation_id,
        )?;
        let source = std::str::from_utf8(&bytes).map_err(|_| ResourceError::Unsupported)?;
        if source.contains('\0') {
            return Err(ResourceError::Unsupported);
        }
        let (result, hunks) = apply_unified_diff(source, &params.patch)?;
        if result.len() as u64 > mount.max_file_bytes {
            return Err(ResourceError::Limit);
        }
        self.check_cancelled(&params.context.operation_id)?;
        let mut candidate = mount.create_candidate().map_err(map_mount_error)?;
        candidate
            .reserve_bytes(result.len() as u64)
            .map_err(map_mount_error)?;
        candidate
            .file
            .write_all(result.as_bytes())
            .map_err(|_| ResourceError::Io)?;
        set_permissions_from(&candidate.file, &opened.metadata).map_err(|_| ResourceError::Io)?;
        let expected_digest = format!("{:x}", Sha256::digest(result.as_bytes()));
        self.check_cancelled(&params.context.operation_id)?;
        let mut info = commit_candidate_locked(
            &mount,
            &target,
            FileWriteMode::Replace,
            Some(&current),
            &mut candidate,
            result.len() as u64,
            &expected_digest,
        )?;
        info.path = params.path.clone();
        Ok((info, hunks))
    }

    pub(crate) fn copy(
        &self,
        mounts: &MountRegistry,
        params: &FileCopyParams,
    ) -> Result<(FileInfo, u64, FileCopySourceStability), ResourceError> {
        let source_mount = mounts
            .get(&params.source.mount_id)
            .ok_or(ResourceError::Denied)?;
        if !source_mount.allows("open_reader") {
            return Err(ResourceError::Denied);
        }
        let destination_mount = write_mount(mounts, &params.destination, "copy")?;
        let mut source = source_mount
            .open_regular(&params.source)
            .map_err(map_mount_error)?;
        let source_revision = file_revision(&source.metadata);
        if params
            .expected_source_revision
            .as_ref()
            .is_some_and(|expected| expected != &source_revision)
        {
            return Err(ResourceError::Conflict);
        }
        let destination = observe_regular(&destination_mount, &params.destination)?;
        if params
            .expected_destination_revision
            .as_ref()
            .is_some_and(|expected| {
                destination.as_ref().map(|(revision, _)| revision) != Some(expected)
            })
        {
            return Err(ResourceError::Conflict);
        }
        if destination.is_some() && !params.replace {
            return Err(ResourceError::Conflict);
        }
        if source.metadata.len() > destination_mount.max_file_bytes {
            return Err(ResourceError::Limit);
        }
        let mut candidate = destination_mount
            .create_candidate()
            .map_err(map_mount_error)?;
        let (bytes, expected_digest) = copy_with_digest(
            &mut source.file,
            &mut candidate,
            destination_mount.max_file_bytes,
            &self.inner.operations,
            &params.context.operation_id,
        )?;
        set_permissions_from(&candidate.file, &source.metadata).map_err(|_| ResourceError::Io)?;
        let final_source = source.file.metadata().map_err(|_| ResourceError::Io)?;
        if file_revision(&final_source) != source_revision {
            return Err(ResourceError::Conflict);
        }
        self.check_cancelled(&params.context.operation_id)?;
        let info = commit_candidate(
            &destination_mount,
            &params.destination,
            if destination.is_some() {
                FileWriteMode::Replace
            } else {
                FileWriteMode::Create
            },
            destination.as_ref().map(|(revision, _)| revision),
            &mut candidate,
            bytes,
            &expected_digest,
        )?;
        Ok((info, bytes, FileCopySourceStability::Verified))
    }

    pub(crate) fn move_path(
        &self,
        mounts: &MountRegistry,
        params: &FileMoveParams,
    ) -> Result<FileInfo, ResourceError> {
        if params.source.mount_id != params.destination.mount_id {
            return Err(ResourceError::Unsupported);
        }
        let mount = write_mount(mounts, &params.source, "move")?;
        let _mutation = mount.mutation_guard();
        let source = mount
            .metadata(&params.source, false)
            .map_err(map_mount_error)?;
        let source_revision = cap_file_revision(&source);
        let source_identity = cap_entry_identity(&source)?;
        if params
            .expected_source_revision
            .as_ref()
            .is_some_and(|expected| expected != &source_revision)
        {
            return Err(ResourceError::Conflict);
        }
        let destination = match mount.metadata(&params.destination, false) {
            Ok(metadata) => Some(metadata),
            Err(MountPathError::NotFound) => None,
            Err(error) => return Err(map_mount_error(error)),
        };
        if params
            .expected_destination_revision
            .as_ref()
            .is_some_and(|expected| {
                destination.as_ref().map(cap_file_revision).as_ref() != Some(expected)
            })
        {
            return Err(ResourceError::Conflict);
        }
        if destination.is_some() && !params.replace {
            return Err(ResourceError::Conflict);
        }
        self.check_cancelled(&params.context.operation_id)?;
        mount
            .rename_within(&params.source, &params.destination, params.replace)
            .map_err(map_mount_error)?;
        let metadata = mount
            .metadata(&params.destination, false)
            .map_err(|_| ResourceError::UnknownOutcome)?;
        if cap_entry_identity(&metadata).map_err(|_| ResourceError::UnknownOutcome)?
            != source_identity
        {
            return Err(ResourceError::UnknownOutcome);
        }
        Ok(cap_file_info(&params.destination, &metadata))
    }

    pub(crate) fn remove(
        &self,
        mounts: &MountRegistry,
        params: &FileRemoveParams,
    ) -> Result<u64, ResourceError> {
        let mount = write_mount(mounts, &params.path, "remove")?;
        let _mutation = mount.mutation_guard();
        let relative = mount.relative_path(&params.path).map_err(map_mount_error)?;
        if relative == Path::new(".") || params.max_entries == 0 {
            return Err(ResourceError::Denied);
        }
        let metadata = mount
            .metadata(&params.path, false)
            .map_err(map_mount_error)?;
        let info = cap_file_info(&params.path, &metadata);
        if info.kind != params.expected_kind {
            return Err(ResourceError::Conflict);
        }
        if params
            .expected_revision
            .as_ref()
            .is_some_and(|expected| info.revision.as_ref() != Some(expected))
        {
            return Err(ResourceError::Conflict);
        }
        if metadata.is_dir() {
            if params.recursive {
                let plan = build_remove_plan(
                    &mount,
                    &relative,
                    params.max_entries.min(MAX_TRAVERSAL_ENTRIES as u64),
                    &self.inner.operations,
                    &params.context.operation_id,
                )?;
                let mut removed = 0_u64;
                for entry in plan {
                    let step = (|| {
                        self.check_cancelled(&params.context.operation_id)?;
                        let current = mount
                            .root
                            .symlink_metadata(&entry.relative)
                            .map_err(|_| ResourceError::Conflict)?;
                        if cap_entry_identity(&current)? != entry.identity {
                            return Err(ResourceError::Conflict);
                        }
                        if entry.directory {
                            mount.remove_dir(&entry.relative).map_err(map_mount_error)?;
                        } else {
                            mount
                                .remove_file(&entry.relative)
                                .map_err(map_mount_error)?;
                        }
                        Ok(())
                    })();
                    if let Err(cause) = step {
                        return Err(if removed == 0 {
                            cause
                        } else {
                            ResourceError::PartialRemove {
                                removed_entries: removed,
                                cause: Box::new(cause),
                            }
                        });
                    }
                    removed += 1;
                }
                Ok(removed)
            } else {
                self.check_cancelled(&params.context.operation_id)?;
                mount.remove_dir(&relative).map_err(map_mount_error)?;
                Ok(1)
            }
        } else {
            self.check_cancelled(&params.context.operation_id)?;
            mount.remove_file(&relative).map_err(map_mount_error)?;
            Ok(1)
        }
    }

    pub(crate) fn expire(&self) {
        self.state().prune(Instant::now(), &self.inner.quota);
    }

    pub(crate) fn release_cursor(&self, cursor: &OutputCursor) -> bool {
        let mut state = self.state();
        state.prune(Instant::now(), &self.inner.quota);
        if let Some(record) = state.cursors.remove(&cursor.0) {
            state.release_snapshot_cursor(&record.snapshot_id, &self.inner.quota);
            state.remember_cursor_release(cursor.0.clone());
            true
        } else {
            state.released_cursors.contains(&cursor.0)
        }
    }

    fn finish_entries(
        &self,
        shape: String,
        mut entries: Vec<FileListEntry>,
        page_limit: u64,
        policy: Option<&OutputPolicy>,
    ) -> Result<FileListResult, ResourceError> {
        let original_items = entries.len();
        let retained_items = bounded_item_count(
            &entries,
            policy.map_or(self.inner.max_output_bytes, |policy| {
                policy.max_output_bytes
            }),
        )?;
        if retained_items < original_items
            && policy.is_some_and(|policy| policy.overflow == OutputOverflow::Fail)
        {
            return Err(ResourceError::OutputLimit);
        }
        entries.truncate(retained_items);
        let dropped_items = (original_items - retained_items) as u64;
        let (items, next, encoded) = page_slice(&entries, 0, page_limit)?;
        if next < entries.len()
            && policy.is_some_and(|policy| policy.overflow == OutputOverflow::Fail)
        {
            return Err(ResourceError::OutputLimit);
        }
        let retained_items = entries.len();
        let next_cursor = if next == retained_items {
            None
        } else {
            self.insert_snapshot(
                shape,
                SnapshotPayload::Entries(Arc::new(entries)),
                next,
                dropped_items,
            )?
        };
        let dropped_items = dropped_items
            + if next < retained_items && next_cursor.is_none() {
                (retained_items - next) as u64
            } else {
                0
            };
        let content_complete = next == retained_items && dropped_items == 0;
        let emitted = items.len() as u64;
        let expires_at = self.cursor_expiry(next_cursor.as_ref());
        Ok(FileListResult {
            entries: items,
            output: disposition(
                emitted,
                encoded,
                next_cursor,
                expires_at,
                content_complete,
                dropped_items,
            ),
        })
    }

    fn finish_matches(
        &self,
        shape: String,
        mut matches: Vec<FileSearchMatch>,
        page_limit: u64,
        policy: Option<&OutputPolicy>,
    ) -> Result<FileSearchResult, ResourceError> {
        let original_items = matches.len();
        let retained_items = bounded_item_count(
            &matches,
            policy.map_or(self.inner.max_output_bytes, |policy| {
                policy.max_output_bytes
            }),
        )?;
        if retained_items < original_items
            && policy.is_some_and(|policy| policy.overflow == OutputOverflow::Fail)
        {
            return Err(ResourceError::OutputLimit);
        }
        matches.truncate(retained_items);
        let dropped_items = (original_items - retained_items) as u64;
        let (items, next, encoded) = page_slice(&matches, 0, page_limit)?;
        if next < matches.len()
            && policy.is_some_and(|policy| policy.overflow == OutputOverflow::Fail)
        {
            return Err(ResourceError::OutputLimit);
        }
        let retained_items = matches.len();
        let next_cursor = if next == retained_items {
            None
        } else {
            self.insert_snapshot(
                shape,
                SnapshotPayload::Matches(Arc::new(matches)),
                next,
                dropped_items,
            )?
        };
        let dropped_items = dropped_items
            + if next < retained_items && next_cursor.is_none() {
                (retained_items - next) as u64
            } else {
                0
            };
        let content_complete = next == retained_items && dropped_items == 0;
        let emitted = items.len() as u64;
        let expires_at = self.cursor_expiry(next_cursor.as_ref());
        Ok(FileSearchResult {
            matches: items,
            output: disposition(
                emitted,
                encoded,
                next_cursor,
                expires_at,
                content_complete,
                dropped_items,
            ),
        })
    }

    fn page_limit(&self, policy: Option<&OutputPolicy>) -> Result<u64, ResourceError> {
        if let Some(policy) = policy
            && (policy.max_inline_bytes == 0
                || policy.max_output_bytes == 0
                || policy.max_inline_bytes > policy.max_output_bytes
                || policy.max_inline_bytes > self.inner.max_inline_bytes
                || policy.max_output_bytes > self.inner.max_output_bytes)
        {
            return Err(ResourceError::Invalid);
        }
        Ok(policy
            .map_or(self.inner.max_inline_bytes, |policy| {
                policy.max_inline_bytes
            })
            .min(
                self.inner
                    .max_response_bytes
                    .saturating_sub(RESPONSE_RESERVE_BYTES),
            )
            .max(1))
    }

    fn insert_snapshot(
        &self,
        shape: String,
        payload: SnapshotPayload,
        offset: usize,
        dropped_items: u64,
    ) -> Result<Option<OutputCursor>, ResourceError> {
        let charged_bytes = payload.encoded_bytes()?;
        let mut state = self.state();
        state.prune(Instant::now(), &self.inner.quota);
        if !self.inner.quota.reserve(charged_bytes, 2) {
            return Ok(None);
        }
        let snapshot_id = match self.inner.selector_ids.next("snapshot") {
            Ok(selector) => selector,
            Err(_) => {
                self.inner.quota.release(charged_bytes, 2);
                return Err(ResourceError::Internal);
            }
        };
        let cursor = match self.inner.selector_ids.next("cursor") {
            Ok(selector) => selector,
            Err(_) => {
                self.inner.quota.release(charged_bytes, 2);
                return Err(ResourceError::Internal);
            }
        };
        let expires_at = Instant::now() + self.inner.ttl;
        let expires_at_utc = match chrono::Duration::from_std(self.inner.ttl) {
            Ok(ttl) => chrono::Utc::now() + ttl,
            Err(_) => {
                self.inner.quota.release(charged_bytes, 2);
                return Err(ResourceError::Internal);
            }
        };
        state.snapshots.insert(
            snapshot_id.clone(),
            SnapshotRecord {
                payload,
                charged_bytes,
                expires_at,
                expires_at_utc,
                cursors: 1,
                dropped_items,
            },
        );
        state.cursors.insert(
            cursor.clone(),
            StructuredCursorRecord {
                snapshot_id,
                shape,
                offset,
                expires_at,
            },
        );
        Ok(Some(OutputCursor(cursor)))
    }

    fn entries_cursor(
        &self,
        cursor: &OutputCursor,
        shape: &str,
    ) -> Result<StructuredSnapshot<FileListEntry>, ResourceError> {
        let state = self.state();
        let record = state
            .cursors
            .get(&cursor.0)
            .ok_or(ResourceError::InvalidHandle)?;
        if record.expires_at <= Instant::now() || record.shape != shape {
            return Err(ResourceError::Conflict);
        }
        let snapshot = state
            .snapshots
            .get(&record.snapshot_id)
            .ok_or(ResourceError::InvalidHandle)?;
        let SnapshotPayload::Entries(entries) = &snapshot.payload else {
            return Err(ResourceError::Conflict);
        };
        Ok(StructuredSnapshot {
            snapshot_id: record.snapshot_id.clone(),
            offset: record.offset,
            items: Arc::clone(entries),
            dropped_items: snapshot.dropped_items,
        })
    }

    fn matches_cursor(
        &self,
        cursor: &OutputCursor,
        shape: &str,
    ) -> Result<StructuredSnapshot<FileSearchMatch>, ResourceError> {
        let state = self.state();
        let record = state
            .cursors
            .get(&cursor.0)
            .ok_or(ResourceError::InvalidHandle)?;
        if record.expires_at <= Instant::now() || record.shape != shape {
            return Err(ResourceError::Conflict);
        }
        let snapshot = state
            .snapshots
            .get(&record.snapshot_id)
            .ok_or(ResourceError::InvalidHandle)?;
        let SnapshotPayload::Matches(matches) = &snapshot.payload else {
            return Err(ResourceError::Conflict);
        };
        Ok(StructuredSnapshot {
            snapshot_id: record.snapshot_id.clone(),
            offset: record.offset,
            items: Arc::clone(matches),
            dropped_items: snapshot.dropped_items,
        })
    }

    fn next_structured_cursor(
        &self,
        snapshot_id: &str,
        shape: &str,
        offset: usize,
        total: usize,
    ) -> Result<Option<OutputCursor>, ResourceError> {
        if offset >= total {
            return Ok(None);
        }
        let mut state = self.state();
        state.prune(Instant::now(), &self.inner.quota);
        let expires_at = state
            .snapshots
            .get(snapshot_id)
            .ok_or(ResourceError::InvalidHandle)?
            .expires_at;
        if !self.inner.quota.reserve(0, 1) {
            return Ok(None);
        }
        let cursor = match self.inner.selector_ids.next("cursor") {
            Ok(selector) => selector,
            Err(_) => {
                self.inner.quota.release(0, 1);
                return Err(ResourceError::Internal);
            }
        };
        state
            .snapshots
            .get_mut(snapshot_id)
            .ok_or(ResourceError::InvalidHandle)?
            .cursors += 1;
        state.cursors.insert(
            cursor.clone(),
            StructuredCursorRecord {
                snapshot_id: snapshot_id.to_owned(),
                shape: shape.to_owned(),
                offset,
                expires_at,
            },
        );
        Ok(Some(OutputCursor(cursor)))
    }

    fn insert_text_cursor(
        &self,
        record: TextCursorRecord,
    ) -> Result<FileTextCursor, ResourceError> {
        let mut state = self.state();
        state.prune(Instant::now(), &self.inner.quota);
        if !self.inner.quota.reserve(0, 1) {
            return Err(ResourceError::Busy);
        }
        let cursor = match self.inner.selector_ids.next("text") {
            Ok(selector) => selector,
            Err(_) => {
                self.inner.quota.release(0, 1);
                return Err(ResourceError::Internal);
            }
        };
        state.text_cursors.insert(cursor.clone(), record);
        Ok(FileTextCursor(cursor))
    }

    fn consume_text_cursor(
        &self,
        cursor: &FileTextCursor,
        path: &EIPPath,
        revision: &FileRevision,
    ) -> Result<(u64, u64, u64), ResourceError> {
        let mut state = self.state();
        state.prune(Instant::now(), &self.inner.quota);
        let record = state
            .text_cursors
            .get(&cursor.0)
            .ok_or(ResourceError::InvalidHandle)?;
        if &record.path != path || &record.revision != revision {
            return Err(ResourceError::Conflict);
        }
        Ok((record.offset, record.line, record.byte_column))
    }

    fn cursor_expiry(
        &self,
        cursor: Option<&OutputCursor>,
    ) -> Option<chrono::DateTime<chrono::Utc>> {
        let cursor = cursor?;
        let state = self.state();
        let record = state.cursors.get(&cursor.0)?;
        state
            .snapshots
            .get(&record.snapshot_id)
            .map(|snapshot| snapshot.expires_at_utc)
    }

    fn check_cancelled(&self, operation_id: &str) -> Result<(), ResourceError> {
        check_operation(&self.inner.operations, operation_id)
    }

    fn state(&self) -> std::sync::MutexGuard<'_, ResourceState> {
        self.inner
            .state
            .lock()
            .unwrap_or_else(PoisonError::into_inner)
    }
}

impl ResourceState {
    fn prune(&mut self, now: Instant, quota: &RetentionQuota) {
        let expired = self
            .cursors
            .iter()
            .filter(|(_, record)| record.expires_at <= now)
            .map(|(cursor, record)| (cursor.clone(), record.snapshot_id.clone()))
            .collect::<Vec<_>>();
        for (cursor, snapshot_id) in expired {
            if self.cursors.remove(&cursor).is_some() {
                self.release_snapshot_cursor(&snapshot_id, quota);
            }
        }
        let expired_text = self
            .text_cursors
            .iter()
            .filter(|(_, record)| record.expires_at <= now)
            .map(|(cursor, _)| cursor.clone())
            .collect::<Vec<_>>();
        for cursor in expired_text {
            if self.text_cursors.remove(&cursor).is_some() {
                quota.release(0, 1);
            }
        }
        let orphaned = self
            .snapshots
            .iter()
            .filter(|(_, snapshot)| snapshot.cursors == 0 || snapshot.expires_at <= now)
            .map(|(id, _)| id.clone())
            .collect::<Vec<_>>();
        for id in orphaned {
            let dependent = self
                .cursors
                .values()
                .filter(|cursor| cursor.snapshot_id == id)
                .count();
            self.cursors.retain(|_, cursor| cursor.snapshot_id != id);
            if let Some(snapshot) = self.snapshots.remove(&id) {
                quota.release(snapshot.charged_bytes, 1 + dependent);
            }
        }
    }

    fn release_snapshot_cursor(&mut self, snapshot_id: &str, quota: &RetentionQuota) {
        quota.release(0, 1);
        if let Some(snapshot) = self.snapshots.get_mut(snapshot_id) {
            snapshot.cursors = snapshot.cursors.saturating_sub(1);
            if snapshot.cursors == 0 {
                let charged = snapshot.charged_bytes;
                self.snapshots.remove(snapshot_id);
                quota.release(charged, 1);
            }
        }
    }

    fn remember_cursor_release(&mut self, cursor: String) {
        if self.released_cursors.insert(cursor.clone()) {
            self.released_order.push_back(cursor);
        }
        while self.released_order.len() > MAX_TRAVERSAL_ENTRIES {
            if let Some(expired) = self.released_order.pop_front() {
                self.released_cursors.remove(&expired);
            }
        }
    }
}

impl SnapshotPayload {
    fn encoded_bytes(&self) -> Result<u64, ResourceError> {
        match self {
            Self::Entries(entries) => encoded_items(entries.as_slice()),
            Self::Matches(matches) => encoded_items(matches.as_slice()),
        }
    }
}

fn read_mount(
    mounts: &MountRegistry,
    path: &EIPPath,
    operation: &str,
) -> Result<Arc<Mount>, ResourceError> {
    mounts
        .get(&path.mount_id)
        .filter(|mount| mount.allows(operation))
        .ok_or(ResourceError::Denied)
}

fn write_mount(
    mounts: &MountRegistry,
    path: &EIPPath,
    operation: &str,
) -> Result<Arc<Mount>, ResourceError> {
    mounts
        .get(&path.mount_id)
        .filter(|mount| mount.writable && mount.allows(operation))
        .ok_or(ResourceError::Denied)
}

fn observe_regular(
    mount: &Arc<Mount>,
    path: &EIPPath,
) -> Result<Option<(FileRevision, std::fs::Metadata)>, ResourceError> {
    match mount.metadata(path, false) {
        Ok(metadata) if metadata.is_symlink() => return Err(ResourceError::Denied),
        Ok(metadata) if !metadata.is_file() => return Err(ResourceError::Denied),
        Ok(_) => {}
        Err(MountPathError::NotFound) => return Ok(None),
        Err(error) => return Err(map_mount_error(error)),
    }
    let opened = mount.open_regular(path).map_err(map_mount_error)?;
    Ok(Some((file_revision(&opened.metadata), opened.metadata)))
}

fn validate_write_mode(
    mode: FileWriteMode,
    current: Option<&(FileRevision, std::fs::Metadata)>,
    expected: Option<&FileRevision>,
) -> Result<(), ResourceError> {
    if expected.is_some_and(|expected| current.map(|(revision, _)| revision) != Some(expected)) {
        return Err(ResourceError::Conflict);
    }
    match mode {
        FileWriteMode::Create if current.is_some() => Err(ResourceError::Conflict),
        FileWriteMode::Replace | FileWriteMode::Append if current.is_none() => {
            Err(ResourceError::NotFound)
        }
        FileWriteMode::Append if expected.is_none() => Err(ResourceError::Conflict),
        _ => Ok(()),
    }
}

fn commit_candidate(
    mount: &Arc<Mount>,
    path: &EIPPath,
    mode: FileWriteMode,
    open_revision: Option<&FileRevision>,
    candidate: &mut StagedCandidate,
    expected_size: u64,
    expected_digest: &str,
) -> Result<FileInfo, ResourceError> {
    let _mutation = mount.mutation_guard();
    commit_candidate_locked(
        mount,
        path,
        mode,
        open_revision,
        candidate,
        expected_size,
        expected_digest,
    )
}

fn commit_candidate_locked(
    mount: &Arc<Mount>,
    path: &EIPPath,
    mode: FileWriteMode,
    open_revision: Option<&FileRevision>,
    candidate: &mut StagedCandidate,
    expected_size: u64,
    expected_digest: &str,
) -> Result<FileInfo, ResourceError> {
    candidate.file.sync_all().map_err(|_| ResourceError::Io)?;
    let metadata = candidate.file.metadata().map_err(|_| ResourceError::Io)?;
    if metadata.len() != expected_size || file_has_multiple_links(&metadata) {
        return Err(ResourceError::Conflict);
    }
    candidate
        .file
        .seek(SeekFrom::Start(0))
        .map_err(|_| ResourceError::Io)?;
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        let read = candidate
            .file
            .read(&mut buffer)
            .map_err(|_| ResourceError::Io)?;
        if read == 0 {
            break;
        }
        hasher.update(&buffer[..read]);
    }
    if format!("{:x}", hasher.finalize()) != expected_digest {
        return Err(ResourceError::Conflict);
    }
    let current = observe_regular(mount, path)?;
    if current.as_ref().map(|(revision, _)| revision) != open_revision {
        return Err(ResourceError::Conflict);
    }
    let replace = match mode {
        FileWriteMode::Create => false,
        FileWriteMode::Replace | FileWriteMode::Append => true,
        FileWriteMode::Upsert => current.is_some(),
    };
    mount
        .publish_candidate(candidate, path, replace)
        .map_err(map_mount_error)?;
    let candidate_metadata = candidate
        .file
        .metadata()
        .map_err(|_| ResourceError::UnknownOutcome)?;
    let opened = mount
        .open_regular(path)
        .map_err(|_| ResourceError::UnknownOutcome)?;
    if file_revision(&candidate_metadata) != file_revision(&opened.metadata) {
        return Err(ResourceError::UnknownOutcome);
    }
    Ok(file_info(path, &opened.metadata))
}

fn copy_with_digest(
    source: &mut std::fs::File,
    destination: &mut StagedCandidate,
    max_bytes: u64,
    operations: &OperationRegistry,
    operation_id: &str,
) -> Result<(u64, String), ResourceError> {
    let mut total = 0_u64;
    let mut hasher = Sha256::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        check_operation(operations, operation_id)?;
        let read = source.read(&mut buffer).map_err(|_| ResourceError::Io)?;
        if read == 0 {
            break;
        }
        let next = total.checked_add(read as u64).ok_or(ResourceError::Limit)?;
        if next > max_bytes {
            return Err(ResourceError::Limit);
        }
        destination
            .reserve_bytes(read as u64)
            .map_err(map_mount_error)?;
        destination
            .file
            .write_all(&buffer[..read])
            .map_err(|_| ResourceError::Io)?;
        hasher.update(&buffer[..read]);
        total = next;
    }
    Ok((total, format!("{:x}", hasher.finalize())))
}

#[cfg(unix)]
fn file_has_multiple_links(metadata: &std::fs::Metadata) -> bool {
    use std::os::unix::fs::MetadataExt;
    metadata.nlink() != 1
}

#[cfg(not(unix))]
fn file_has_multiple_links(_metadata: &std::fs::Metadata) -> bool {
    false
}

fn check_operation(
    operations: &OperationRegistry,
    operation_id: &str,
) -> Result<(), ResourceError> {
    match operations.interruption(operation_id) {
        Some(OperationInterruption::Cancelled) => Err(ResourceError::Cancelled),
        Some(OperationInterruption::TimedOut) => Err(ResourceError::Timeout),
        None => Ok(()),
    }
}

fn build_remove_plan(
    mount: &Arc<Mount>,
    root: &Path,
    max_entries: u64,
    operations: &OperationRegistry,
    operation_id: &str,
) -> Result<Vec<RemovePlanEntry>, ResourceError> {
    if max_entries == 0 {
        return Err(ResourceError::Limit);
    }
    let mut pending = vec![(root.to_path_buf(), 0_u32, false, None)];
    let mut plan = Vec::new();
    let mut discovered = 0_u64;
    let mut visited = BTreeSet::new();
    while let Some((relative, depth, expanded, saved_identity)) = pending.pop() {
        check_operation(operations, operation_id)?;
        if expanded {
            plan.push(RemovePlanEntry {
                relative,
                directory: true,
                identity: saved_identity.ok_or(ResourceError::Internal)?,
            });
            continue;
        }
        discovered = discovered.checked_add(1).ok_or(ResourceError::Limit)?;
        if discovered > max_entries || discovered > MAX_TRAVERSAL_ENTRIES as u64 {
            return Err(ResourceError::Limit);
        }
        let metadata = mount
            .root
            .symlink_metadata(&relative)
            .map_err(|_| ResourceError::Conflict)?;
        let identity = cap_entry_identity(&metadata)?;
        if !metadata.is_dir() || metadata.is_symlink() {
            plan.push(RemovePlanEntry {
                relative,
                directory: false,
                identity,
            });
            continue;
        }
        if !visited.insert(identity) {
            return Err(ResourceError::Conflict);
        }
        let reader = mount
            .root
            .read_dir(&relative)
            .map_err(|_| ResourceError::Io)?;
        let mut children = reader
            .map(|entry| {
                let entry = entry.map_err(|_| ResourceError::Io)?;
                Ok(relative.join(entry.file_name()))
            })
            .collect::<Result<Vec<_>, ResourceError>>()?;
        if depth >= MAX_TRAVERSAL_DEPTH && !children.is_empty() {
            return Err(ResourceError::Limit);
        }
        children.sort();
        pending.push((relative, depth, true, Some(identity)));
        pending.extend(
            children
                .into_iter()
                .rev()
                .map(|child| (child, depth + 1, false, None)),
        );
    }
    Ok(plan)
}

#[cfg(unix)]
fn cap_entry_identity(metadata: &cap_std::fs::Metadata) -> Result<CapEntryIdentity, ResourceError> {
    use cap_std::fs::MetadataExt;
    Ok(CapEntryIdentity {
        device: metadata.dev(),
        inode: metadata.ino(),
        file_type: u64::from(metadata.mode()) & u64::from(libc::S_IFMT),
    })
}

#[cfg(not(unix))]
fn cap_entry_identity(
    _metadata: &cap_std::fs::Metadata,
) -> Result<CapEntryIdentity, ResourceError> {
    Err(ResourceError::Unsupported)
}

fn walk_entries(
    mount: &Arc<Mount>,
    root: &EIPPath,
    max_depth: u32,
    follow_symlinks: bool,
    operations: &OperationRegistry,
    operation_id: &str,
) -> Result<Vec<FileListEntry>, ResourceError> {
    let root_relative = mount.relative_path(root).map_err(map_mount_error)?;
    let mut pending = vec![(root_relative, String::new(), 0_u32)];
    let mut entries = Vec::new();
    let mut visited = BTreeSet::new();
    while let Some((directory, prefix, depth)) = pending.pop() {
        check_operation(operations, operation_id)?;
        if depth >= max_depth {
            continue;
        }
        let reader = mount
            .root
            .read_dir(&directory)
            .map_err(|_| ResourceError::Io)?;
        let mut children = Vec::new();
        for entry in reader {
            let entry = entry.map_err(|_| ResourceError::Io)?;
            let name = entry
                .file_name()
                .into_string()
                .map_err(|_| ResourceError::Unsupported)?;
            if name.contains('/') || name.contains('\0') {
                return Err(ResourceError::Unsupported);
            }
            let relative_path = if prefix.is_empty() {
                name
            } else {
                format!("{prefix}/{name}")
            };
            let logical = join_logical(&root.path, &relative_path);
            let path = EIPPath {
                mount_id: root.mount_id.clone(),
                path: logical,
            };
            let metadata = mount
                .metadata(&path, follow_symlinks)
                .map_err(map_mount_error)?;
            let info = cap_file_info(&path, &metadata);
            let child_relative = mount.relative_path(&path).map_err(map_mount_error)?;
            if metadata.is_dir() && !metadata.file_type().is_symlink() {
                let revision = info
                    .revision
                    .as_ref()
                    .map(|revision| revision.0.clone())
                    .unwrap_or_else(|| relative_path.clone());
                if !visited.insert(revision) {
                    return Err(ResourceError::Conflict);
                }
                children.push((child_relative, relative_path.clone(), depth + 1));
            }
            entries.push(FileListEntry {
                relative_path,
                info,
            });
            if entries.len() > MAX_TRAVERSAL_ENTRIES {
                return Err(ResourceError::Limit);
            }
        }
        children.sort_by(|left, right| right.1.as_bytes().cmp(left.1.as_bytes()));
        pending.extend(children);
    }
    entries.sort_by(|left, right| {
        left.relative_path
            .as_bytes()
            .cmp(right.relative_path.as_bytes())
    });
    Ok(entries)
}

fn join_logical(root: &str, relative: &str) -> String {
    if root == "/" {
        format!("/{relative}")
    } else {
        format!("{root}/{relative}")
    }
}

struct TextPage {
    text: String,
    end: TextPosition,
    end_offset: u64,
    complete: bool,
}

fn find_line_offset(
    file: &std::fs::File,
    line: u64,
    file_size: u64,
    operations: &OperationRegistry,
    operation_id: &str,
) -> Result<u64, ResourceError> {
    if line == 1 {
        return Ok(0);
    }
    let mut reader = BufReader::new(file.try_clone().map_err(|_| ResourceError::Io)?);
    let mut current = 1_u64;
    let mut offset = 0_u64;
    while offset < file_size {
        check_operation(operations, operation_id)?;
        let available = reader.fill_buf().map_err(|_| ResourceError::Io)?;
        if available.is_empty() {
            break;
        }
        let consumed = available
            .iter()
            .position(|byte| *byte == b'\n')
            .map_or(available.len(), |position| position + 1);
        let ended_line = available[consumed - 1] == b'\n';
        reader.consume(consumed);
        offset = offset
            .checked_add(consumed as u64)
            .ok_or(ResourceError::Limit)?;
        if ended_line {
            current += 1;
            if current == line {
                return Ok(offset);
            }
        }
    }
    if current == line && offset == file_size {
        Ok(offset)
    } else {
        Err(ResourceError::Invalid)
    }
}

fn read_file_bounded(
    mut file: std::fs::File,
    max_bytes: u64,
    operations: &OperationRegistry,
    operation_id: &str,
) -> Result<Vec<u8>, ResourceError> {
    let mut bytes = Vec::new();
    let mut buffer = [0_u8; 64 * 1024];
    loop {
        check_operation(operations, operation_id)?;
        let read = file.read(&mut buffer).map_err(|_| ResourceError::Io)?;
        if read == 0 {
            return Ok(bytes);
        }
        let next = (bytes.len() as u64)
            .checked_add(read as u64)
            .ok_or(ResourceError::Limit)?;
        if next > max_bytes {
            return Err(ResourceError::Limit);
        }
        bytes.extend_from_slice(&buffer[..read]);
    }
}

fn read_text_page(
    mut file: std::fs::File,
    offset: u64,
    start_line: u64,
    start_column: u64,
    max_bytes: u64,
    max_lines: u64,
    file_size: u64,
) -> Result<TextPage, ResourceError> {
    file.seek(SeekFrom::Start(offset))
        .map_err(|_| ResourceError::Io)?;
    let remaining = file_size.saturating_sub(offset);
    let read_limit = remaining.min(max_bytes.saturating_add(4));
    let mut bytes = Vec::with_capacity(read_limit as usize);
    file.take(read_limit)
        .read_to_end(&mut bytes)
        .map_err(|_| ResourceError::Io)?;
    let nominal = bytes.len().min(max_bytes as usize);
    let mut end = nominal;
    while end > 0 && std::str::from_utf8(&bytes[..end]).is_err() {
        end -= 1;
        if nominal - end > 3 {
            return Err(ResourceError::Unsupported);
        }
    }
    let valid = std::str::from_utf8(&bytes[..end]).map_err(|_| ResourceError::Unsupported)?;
    if valid.contains('\0') {
        return Err(ResourceError::Unsupported);
    }
    let mut selected_end = valid.len();
    if max_lines != u64::MAX {
        let mut lines = 0_u64;
        for (index, byte) in valid.bytes().enumerate() {
            if byte == b'\n' {
                lines += 1;
                if lines >= max_lines {
                    selected_end = index + 1;
                    break;
                }
            }
        }
    }
    let text = valid[..selected_end].to_owned();
    let mut line = start_line;
    let mut column = start_column;
    for byte in text.bytes() {
        if byte == b'\n' {
            line += 1;
            column = 0;
        } else {
            column += 1;
        }
    }
    let end_offset = offset + selected_end as u64;
    Ok(TextPage {
        text,
        end: TextPosition {
            line,
            byte_column: column,
        },
        end_offset,
        complete: end_offset == file_size,
    })
}

fn page_slice<T: Clone + Serialize>(
    items: &[T],
    offset: usize,
    limit: u64,
) -> Result<(Vec<T>, usize, u64), ResourceError> {
    if offset > items.len() {
        return Err(ResourceError::InvalidHandle);
    }
    let mut page = Vec::new();
    let mut encoded = 0_u64;
    let mut next = offset;
    while next < items.len() {
        let size = serde_json::to_vec(&items[next])
            .map_err(|_| ResourceError::Internal)?
            .len() as u64;
        if encoded.saturating_add(size) > limit {
            if page.is_empty() {
                return Err(ResourceError::OutputLimit);
            }
            break;
        }
        encoded += size;
        page.push(items[next].clone());
        next += 1;
    }
    Ok((page, next, encoded))
}

fn disposition(
    emitted_items: u64,
    encoded_bytes: u64,
    cursor: Option<OutputCursor>,
    expires_at: Option<chrono::DateTime<chrono::Utc>>,
    content_complete: bool,
    dropped_items: u64,
) -> StructuredOutputDisposition {
    StructuredOutputDisposition {
        producer_complete: true,
        content_complete,
        emitted_items,
        dropped_items: (dropped_items > 0).then_some(dropped_items),
        encoded_bytes,
        cursor,
        expires_at,
    }
}

fn bounded_item_count<T: Serialize>(items: &[T], limit: u64) -> Result<usize, ResourceError> {
    let mut encoded = 0_u64;
    for (index, item) in items.iter().enumerate() {
        let size = serde_json::to_vec(item)
            .map_err(|_| ResourceError::Internal)?
            .len() as u64;
        let next = encoded.checked_add(size).ok_or(ResourceError::Limit)?;
        if next > limit {
            return Ok(index);
        }
        encoded = next;
    }
    Ok(items.len())
}

fn encoded_items<T: Serialize>(items: &[T]) -> Result<u64, ResourceError> {
    items.iter().try_fold(0_u64, |total, item| {
        let size = serde_json::to_vec(item)
            .map_err(|_| ResourceError::Internal)?
            .len() as u64;
        total.checked_add(size).ok_or(ResourceError::Limit)
    })
}

fn shape_key<T: Serialize>(method: &str, params: &T) -> Result<String, ResourceError> {
    let mut value = serde_json::to_value(params).map_err(|_| ResourceError::Internal)?;
    let object = value.as_object_mut().ok_or(ResourceError::Internal)?;
    object.remove("context");
    object.remove("cursor");
    object.remove("output_policy");
    let mut hasher = Sha256::new();
    hasher.update(method.as_bytes());
    hasher.update([0]);
    hasher.update(serde_json::to_vec(&value).map_err(|_| ResourceError::Internal)?);
    Ok(format!("{:x}", hasher.finalize()))
}

enum PathMatcher {
    Glob(globset::GlobMatcher),
    Regex(Regex),
}

impl PathMatcher {
    fn new(mode: FindMode, pattern: &str) -> Result<Self, ResourceError> {
        match mode {
            FindMode::Glob => {
                validate_glob_pattern(pattern)?;
                Ok(Self::Glob(
                    GlobBuilder::new(pattern)
                        .literal_separator(true)
                        .build()
                        .map_err(|_| ResourceError::Invalid)?
                        .compile_matcher(),
                ))
            }
            FindMode::Regex => Ok(Self::Regex(
                Regex::new(&format!("^(?:{pattern})$")).map_err(|_| ResourceError::Invalid)?,
            )),
        }
    }

    fn matches(&self, path: &str) -> bool {
        match self {
            Self::Glob(glob) => glob.is_match(path),
            Self::Regex(regex) => regex.is_match(path),
        }
    }
}

fn validate_glob_pattern(pattern: &str) -> Result<(), ResourceError> {
    if pattern.contains(['{', '}', '\\'])
        || pattern
            .split('/')
            .any(|segment| segment.contains("**") && segment != "**")
    {
        return Err(ResourceError::Invalid);
    }
    Ok(())
}

fn compile_globs(patterns: &[String]) -> Result<Option<GlobSet>, ResourceError> {
    if patterns.is_empty() {
        return Ok(None);
    }
    let mut builder = GlobSetBuilder::new();
    for pattern in patterns {
        if pattern.len() > MAX_PATTERN_BYTES {
            return Err(ResourceError::Limit);
        }
        validate_glob_pattern(pattern)?;
        builder.add(
            GlobBuilder::new(pattern)
                .literal_separator(true)
                .build()
                .map_err(|_| ResourceError::Invalid)?,
        );
    }
    builder
        .build()
        .map(Some)
        .map_err(|_| ResourceError::Invalid)
}

enum ContentMatcher {
    Literal(String),
    Regex(Regex),
}

impl ContentMatcher {
    fn new(mode: SearchMode, query: &str, case_sensitive: bool) -> Result<Self, ResourceError> {
        match mode {
            SearchMode::Literal if case_sensitive => Ok(Self::Literal(query.to_owned())),
            SearchMode::Literal => Regex::new(&format!("(?i:{})", regex::escape(query)))
                .map(Self::Regex)
                .map_err(|_| ResourceError::Invalid),
            SearchMode::Regex => {
                let pattern = if case_sensitive {
                    query.to_owned()
                } else {
                    format!("(?i:{query})")
                };
                Regex::new(&pattern)
                    .map(Self::Regex)
                    .map_err(|_| ResourceError::Invalid)
            }
        }
    }

    fn ranges(&self, line: &str, max_matches: usize) -> Result<Vec<(usize, usize)>, ResourceError> {
        let ranges = match self {
            Self::Literal(query) => line
                .match_indices(query)
                .map(|(start, value)| (start, start + value.len()))
                .take(max_matches.saturating_add(1))
                .collect::<Vec<_>>(),
            Self::Regex(regex) => regex
                .find_iter(line)
                .map(|matched| (matched.start(), matched.end()))
                .take(max_matches.saturating_add(1))
                .collect::<Vec<_>>(),
        };
        if ranges.len() > max_matches {
            Err(ResourceError::Limit)
        } else {
            Ok(ranges)
        }
    }
}

fn truncate_utf8(value: &str, max_bytes: usize) -> &str {
    if value.len() <= max_bytes {
        return value;
    }
    let mut end = max_bytes;
    while !value.is_char_boundary(end) {
        end -= 1;
    }
    &value[..end]
}

fn search_file(
    mount: &Arc<Mount>,
    path: &EIPPath,
    matcher: &ContentMatcher,
    operations: &OperationRegistry,
    operation_id: &str,
) -> Result<Vec<FileSearchMatch>, ResourceError> {
    let opened = mount.open_regular(path).map_err(map_mount_error)?;
    let mut reader = BufReader::new(opened.file);
    let mut line = Vec::new();
    let mut line_number = 1_u64;
    let mut offset = 0_u64;
    let mut matches = Vec::new();
    loop {
        check_operation(operations, operation_id)?;
        line.clear();
        let read = read_bounded_line(&mut reader, &mut line, MAX_SEARCH_LINE_BYTES)?;
        if read == 0 {
            break;
        }
        if line.contains(&0) {
            return Ok(Vec::new());
        }
        let text = match std::str::from_utf8(&line) {
            Ok(text) => text,
            Err(_) => return Ok(Vec::new()),
        };
        let preview = text.trim_end_matches(['\r', '\n']);
        let remaining = MAX_TRAVERSAL_ENTRIES.saturating_sub(matches.len());
        let ranges = matcher.ranges(preview, remaining)?;
        for (start, _) in ranges {
            matches.push(FileSearchMatch {
                path: path.clone(),
                line_number,
                byte_offset: offset + start as u64,
                preview: truncate_utf8(preview, 4096).to_owned(),
            });
            if matches.len() > MAX_TRAVERSAL_ENTRIES {
                return Err(ResourceError::Limit);
            }
        }
        offset += read as u64;
        line_number += 1;
    }
    Ok(matches)
}

fn read_bounded_line<R: BufRead>(
    reader: &mut R,
    output: &mut Vec<u8>,
    max_bytes: usize,
) -> Result<usize, ResourceError> {
    loop {
        let available = reader.fill_buf().map_err(|_| ResourceError::Io)?;
        if available.is_empty() {
            return Ok(output.len());
        }
        let consumed = available
            .iter()
            .position(|byte| *byte == b'\n')
            .map_or(available.len(), |position| position + 1);
        if output.len().saturating_add(consumed) > max_bytes {
            return Err(ResourceError::Limit);
        }
        let ended_line = available[consumed - 1] == b'\n';
        output.extend_from_slice(&available[..consumed]);
        reader.consume(consumed);
        if ended_line {
            return Ok(output.len());
        }
    }
}

fn apply_unified_diff(source: &str, patch: &str) -> Result<(String, u64), ResourceError> {
    let source_lines = source.split_inclusive('\n').collect::<Vec<_>>();
    let patch_lines = patch.split_inclusive('\n').collect::<Vec<_>>();
    let mut index = 0_usize;
    if patch_lines
        .get(index)
        .is_some_and(|line| line.starts_with("--- "))
    {
        index += 1;
    }
    if patch_lines
        .get(index)
        .is_some_and(|line| line.starts_with("+++ "))
    {
        index += 1;
    }
    let mut output = String::new();
    let mut output_lines = 0_usize;
    let mut source_index = 0_usize;
    let mut hunks = 0_u64;
    while index < patch_lines.len() {
        if hunks >= MAX_PATCH_HUNKS {
            return Err(ResourceError::Limit);
        }
        let header = patch_lines[index].trim_end_matches(['\r', '\n']);
        let (old_start, old_count, new_start, new_count) = parse_hunk_header(header)?;
        let target = if old_count == 0 {
            old_start
        } else {
            old_start.checked_sub(1).ok_or(ResourceError::Invalid)?
        };
        if target < source_index || target > source_lines.len() {
            return Err(ResourceError::Conflict);
        }
        output.extend(source_lines[source_index..target].iter().copied());
        output_lines += target - source_index;
        let new_target = if new_count == 0 {
            new_start
        } else {
            new_start.checked_sub(1).ok_or(ResourceError::Invalid)?
        };
        if new_target != output_lines {
            return Err(ResourceError::Invalid);
        }
        source_index = target;
        index += 1;
        hunks += 1;
        let mut observed_old = 0_usize;
        let mut observed_new = 0_usize;
        while index < patch_lines.len() && !patch_lines[index].starts_with("@@ ") {
            let line = patch_lines[index];
            if line.len() > MAX_PATCH_LINE_BYTES || line.starts_with("\\ No newline at end of file")
            {
                return Err(ResourceError::Invalid);
            }
            let (tag, mut value) = line.split_at(1);
            let no_newline = patch_lines.get(index + 1).is_some_and(|next| {
                next.trim_end_matches(['\r', '\n']) == "\\ No newline at end of file"
            });
            if no_newline {
                value = value
                    .strip_suffix("\r\n")
                    .or_else(|| value.strip_suffix('\n'))
                    .ok_or(ResourceError::Invalid)?;
            }
            match tag {
                " " => {
                    if source_lines.get(source_index).copied() != Some(value) {
                        return Err(ResourceError::Conflict);
                    }
                    output.push_str(value);
                    output_lines += 1;
                    source_index += 1;
                    observed_old += 1;
                    observed_new += 1;
                }
                "-" => {
                    if source_lines.get(source_index).copied() != Some(value) {
                        return Err(ResourceError::Conflict);
                    }
                    source_index += 1;
                    observed_old += 1;
                }
                "+" => {
                    output.push_str(value);
                    output_lines += 1;
                    observed_new += 1;
                }
                _ => return Err(ResourceError::Invalid),
            }
            if observed_old > old_count || observed_new > new_count {
                return Err(ResourceError::Invalid);
            }
            index += if no_newline { 2 } else { 1 };
        }
        if observed_old != old_count || observed_new != new_count {
            return Err(ResourceError::Invalid);
        }
    }
    if hunks == 0 {
        return Err(ResourceError::Invalid);
    }
    output.extend(source_lines[source_index..].iter().copied());
    Ok((output, hunks))
}

fn parse_hunk_header(header: &str) -> Result<(usize, usize, usize, usize), ResourceError> {
    let ranges = header
        .strip_prefix("@@ -")
        .and_then(|rest| rest.split_once(" @@"))
        .ok_or(ResourceError::Invalid)?
        .0;
    let (old, new) = ranges.split_once(" +").ok_or(ResourceError::Invalid)?;
    let (old_start, old_count) = parse_hunk_range(old)?;
    let (new_start, new_count) = parse_hunk_range(new)?;
    Ok((old_start, old_count, new_start, new_count))
}

fn parse_hunk_range(range: &str) -> Result<(usize, usize), ResourceError> {
    let (start, count) = match range.split_once(',') {
        Some((start, count)) => (start, count),
        None => (range, "1"),
    };
    let start = start.parse::<usize>().map_err(|_| ResourceError::Invalid)?;
    let count = count.parse::<usize>().map_err(|_| ResourceError::Invalid)?;
    if (count > 0 && start == 0) || count > MAX_TRAVERSAL_ENTRIES {
        return Err(ResourceError::Invalid);
    }
    Ok((start, count))
}

#[cfg(unix)]
fn set_executable(file: &std::fs::File, executable: bool) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    let mut permissions = file.metadata()?.permissions();
    let mode = permissions.mode();
    permissions.set_mode(if executable {
        mode | 0o100
    } else {
        mode & !0o111
    });
    file.set_permissions(permissions)
}

#[cfg(not(unix))]
fn set_executable(_file: &std::fs::File, executable: bool) -> std::io::Result<()> {
    if executable {
        Err(std::io::Error::new(
            std::io::ErrorKind::Unsupported,
            "executable bits unsupported",
        ))
    } else {
        Ok(())
    }
}

#[cfg(unix)]
fn set_permissions_from(file: &std::fs::File, metadata: &std::fs::Metadata) -> std::io::Result<()> {
    use std::os::unix::fs::PermissionsExt;
    file.set_permissions(std::fs::Permissions::from_mode(
        metadata.permissions().mode() & 0o777,
    ))
}

#[cfg(not(unix))]
fn set_permissions_from(
    _file: &std::fs::File,
    _metadata: &std::fs::Metadata,
) -> std::io::Result<()> {
    Ok(())
}

fn cap_file_info(path: &EIPPath, metadata: &cap_std::fs::Metadata) -> FileInfo {
    let kind = if metadata.is_file() {
        FileKind::File
    } else if metadata.is_dir() {
        FileKind::Directory
    } else if metadata.is_symlink() {
        FileKind::Symlink
    } else {
        FileKind::Other
    };
    FileInfo {
        path: path.clone(),
        kind,
        size_bytes: metadata.is_file().then_some(metadata.len()),
        modified_at: metadata
            .modified()
            .ok()
            .map(cap_std::time::SystemTime::into_std)
            .map(chrono::DateTime::from),
        executable: cap_executable(metadata),
        revision: cfg!(unix).then(|| cap_file_revision(metadata)),
    }
}

fn cap_file_revision(metadata: &cap_std::fs::Metadata) -> FileRevision {
    let mut hasher = Sha256::new();
    #[cfg(unix)]
    {
        use cap_std::fs::MetadataExt;
        for value in [
            metadata.dev(),
            metadata.ino(),
            metadata.len(),
            metadata.mtime() as u64,
            metadata.mtime_nsec() as u64,
            metadata.ctime() as u64,
            metadata.ctime_nsec() as u64,
            metadata.mode() as u64,
        ] {
            hasher.update(value.to_be_bytes());
        }
    }
    #[cfg(not(unix))]
    {
        hasher.update(metadata.len().to_be_bytes());
    }
    FileRevision(format!("r1-{:x}", hasher.finalize()))
}

#[cfg(unix)]
fn cap_executable(metadata: &cap_std::fs::Metadata) -> Option<bool> {
    use cap_std::fs::MetadataExt;
    metadata.is_file().then(|| metadata.mode() & 0o111 != 0)
}

#[cfg(not(unix))]
fn cap_executable(metadata: &cap_std::fs::Metadata) -> Option<bool> {
    metadata.is_file().then_some(false)
}

fn map_mount_error(error: MountPathError) -> ResourceError {
    match error {
        MountPathError::Invalid => ResourceError::Invalid,
        MountPathError::Denied | MountPathError::NotRegular => ResourceError::Denied,
        MountPathError::NotFound => ResourceError::NotFound,
        MountPathError::AlreadyExists => ResourceError::Conflict,
        MountPathError::Limit | MountPathError::Quota => ResourceError::Limit,
        MountPathError::Unsupported => ResourceError::Unsupported,
        MountPathError::UnknownOutcome => ResourceError::UnknownOutcome,
        MountPathError::Io => ResourceError::Io,
        MountPathError::Internal => ResourceError::Internal,
    }
}

#[cfg(test)]
mod tests {
    use std::{fs, path::PathBuf, time::Duration};

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    use crate::eip::{
        FileCopyParams, FileMkdirParams, FileMoveParams, FilePatchTextParams, FileRemoveParams,
        FileWriteMode, FileWriteTextParams,
    };
    use crate::{
        config::{Config, TrustedMountConfig},
        eip::{
            EIPCallContext, EIPPath, FileFindParams, FileKind, FileListParams, FileReadTextParams,
            FileSearchParams, FileStatParams, FindMode, OutputOverflow, OutputPolicy, SearchMode,
        },
        mount::MountRegistry,
        operation::{OperationRegistry, random_selector},
        retention::RetentionQuota,
    };

    use super::{ResourceError, ResourceRegistry, apply_unified_diff, join_logical};

    struct TempTree(PathBuf);

    impl TempTree {
        fn new() -> Self {
            let path = std::env::temp_dir().join(
                random_selector("agent-envd-resource-test").expect("random temporary directory"),
            );
            fs::create_dir(&path).expect("creates temporary directory");
            Self(path)
        }

        fn child(&self, name: &str) -> PathBuf {
            self.0.join(name)
        }
    }

    impl Drop for TempTree {
        fn drop(&mut self) {
            let _ = fs::remove_dir_all(&self.0);
        }
    }

    struct Fixture {
        _tree: TempTree,
        native: PathBuf,
        mounts: MountRegistry,
        resources: ResourceRegistry,
    }

    impl Fixture {
        #[cfg(any(target_os = "linux", target_os = "macos"))]
        fn new() -> Self {
            Self::with_mount(true, 64 * 1024 * 1024, 64)
        }

        fn read_only() -> Self {
            Self::with_mount(false, 64 * 1024 * 1024, 64)
        }

        #[cfg(any(target_os = "linux", target_os = "macos"))]
        fn with_staging_limits(max_bytes: u64, max_objects: u64) -> Self {
            Self::with_mount(true, max_bytes, max_objects)
        }

        fn with_mount(writable: bool, max_bytes: u64, max_objects: u64) -> Self {
            let tree = TempTree::new();
            let native = tree.child("native");
            fs::create_dir(&native).expect("native root");
            let staging_root = if writable {
                let staging = tree.child("staging");
                fs::create_dir(&staging).expect("staging root");
                #[cfg(unix)]
                {
                    use std::os::unix::fs::PermissionsExt;
                    fs::set_permissions(&staging, fs::Permissions::from_mode(0o700))
                        .expect("private staging permissions");
                }
                Some(staging)
            } else {
                None
            };
            let mut config = Config::for_test("env-resource-test");
            config.limits.max_staged_file_bytes = max_bytes;
            config.limits.max_staged_file_objects = max_objects;
            config.mounts.push(TrustedMountConfig {
                mount_id: "workspace".to_owned(),
                native_root: native.clone(),
                staging_root,
                writable,
                exclusive_mutation_control: writable,
                allow_command_execution: false,
                max_file_bytes: 1024 * 1024,
                allowed_operations: Vec::new(),
            });
            let operations = OperationRegistry::new(
                config.environment_id.clone(),
                7,
                256,
                Duration::from_secs(60),
                Duration::from_secs(60),
            );
            let mounts = MountRegistry::initialize(&config).expect("mounts initialize");
            let quota = RetentionQuota::new(&config).expect("retention quota");
            let resources =
                ResourceRegistry::new(&config, operations, quota).expect("resources initialize");
            Self {
                _tree: tree,
                native,
                mounts,
                resources,
            }
        }
    }

    fn context(operation_id: &str) -> EIPCallContext {
        EIPCallContext {
            operation_id: operation_id.to_owned(),
            deadline: None,
            idempotency_key: None,
        }
    }

    fn path(value: &str) -> EIPPath {
        EIPPath {
            mount_id: "workspace".to_owned(),
            path: value.to_owned(),
        }
    }

    #[test]
    fn applies_strict_unified_diff() {
        let patch = "--- a/file\n+++ b/file\n@@ -1,2 +1,2 @@\n one\n-two\n+three\n";
        let (result, hunks) = apply_unified_diff("one\ntwo\n", patch).expect("patch applies");
        assert_eq!(result, "one\nthree\n");
        assert_eq!(hunks, 1);
    }

    #[test]
    fn joins_mount_relative_paths() {
        assert_eq!(join_logical("/", "a/b"), "/a/b");
        assert_eq!(join_logical("/root", "a"), "/root/a");
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    #[test]
    fn resource_candidates_enforce_and_release_shared_staging_quota() {
        let fixture = Fixture::with_staging_limits(8, 1);
        let oversized = fixture.resources.write_text(
            &fixture.mounts,
            &FileWriteTextParams {
                context: context("oversized-staging-write"),
                path: path("/oversized.txt"),
                mode: FileWriteMode::Create,
                text: "123456789".to_owned(),
                expected_revision: None,
                executable: None,
            },
        );
        assert_eq!(oversized, Err(ResourceError::Limit));
        assert!(!fixture.native.join("oversized.txt").exists());

        let written = fixture
            .resources
            .write_text(
                &fixture.mounts,
                &FileWriteTextParams {
                    context: context("bounded-staging-write"),
                    path: path("/bounded.txt"),
                    mode: FileWriteMode::Create,
                    text: "12345678".to_owned(),
                    expected_revision: None,
                    executable: None,
                },
            )
            .expect("failed candidate deletion returns quota to the shared manager");
        assert_eq!(written.1, 8);
        assert_eq!(
            fs::read_to_string(fixture.native.join("bounded.txt")).expect("bounded destination"),
            "12345678"
        );
    }

    #[test]
    fn observes_text_and_structured_resources_with_stable_cursors() {
        let fixture = Fixture::read_only();
        fs::create_dir(fixture.native.join("docs")).expect("docs directory");
        fs::write(fixture.native.join("docs/main.txt"), "alpha\nbeta\n").expect("text fixture");
        for index in 0..8 {
            fs::write(
                fixture.native.join(format!("docs/item-{index}.txt")),
                format!("needle {index}\n"),
            )
            .expect("search fixture");
        }

        let stat = fixture
            .resources
            .stat(
                &fixture.mounts,
                &FileStatParams {
                    context: context("stat"),
                    path: path("/docs/main.txt"),
                    follow_symlinks: true,
                },
            )
            .expect("stat succeeds");
        assert_eq!(stat.info.kind, FileKind::File);
        assert_eq!(stat.info.revision.is_some(), cfg!(unix));

        let first = fixture
            .resources
            .read_text(
                &fixture.mounts,
                &FileReadTextParams {
                    context: context("text-1"),
                    path: path("/docs/main.txt"),
                    cursor: None,
                    start_line: None,
                    max_lines: None,
                    max_bytes: Some(5),
                    expected_revision: stat.info.revision.clone(),
                },
            )
            .expect("first text page");
        assert_eq!(first.text, "alpha");
        assert!(!first.content_complete);
        #[cfg(unix)]
        {
            let second = fixture
                .resources
                .read_text(
                    &fixture.mounts,
                    &FileReadTextParams {
                        context: context("text-2"),
                        path: path("/docs/main.txt"),
                        cursor: first.next_cursor,
                        start_line: None,
                        max_lines: None,
                        max_bytes: Some(64),
                        expected_revision: None,
                    },
                )
                .expect("second text page");
            assert_eq!(second.text, "\nbeta\n");
            assert!(second.content_complete);
        }
        #[cfg(not(unix))]
        {
            assert!(first.next_cursor.is_none());
            let restarted = fixture
                .resources
                .read_text(
                    &fixture.mounts,
                    &FileReadTextParams {
                        context: context("text-2"),
                        path: path("/docs/main.txt"),
                        cursor: None,
                        start_line: None,
                        max_lines: None,
                        max_bytes: Some(64),
                        expected_revision: None,
                    },
                )
                .expect("separate text observation");
            assert_eq!(restarted.text, "alpha\nbeta\n");
            assert!(restarted.content_complete);
        }

        let policy = OutputPolicy {
            max_inline_bytes: 700,
            max_output_bytes: 4096,
            overflow: OutputOverflow::Truncate,
        };
        let first_list = fixture
            .resources
            .list(
                &fixture.mounts,
                &FileListParams {
                    context: context("list-1"),
                    path: path("/docs"),
                    recursive: false,
                    max_depth: 1,
                    cursor: None,
                    output_policy: Some(policy.clone()),
                },
            )
            .expect("first list page");
        let cursor = first_list
            .output
            .cursor
            .clone()
            .expect("continuation cursor");
        assert!(first_list.output.producer_complete);
        assert!(!first_list.output.content_complete);
        assert_eq!(first_list.output.dropped_items, None);
        assert!(first_list.output.expires_at.is_some());
        let continuation_params = FileListParams {
            context: context("list-2"),
            path: path("/docs"),
            recursive: false,
            max_depth: 1,
            cursor: Some(cursor.clone()),
            output_policy: Some(policy),
        };
        let continued = fixture
            .resources
            .list(&fixture.mounts, &continuation_params)
            .expect("list continuation");
        let repeated = fixture
            .resources
            .list(&fixture.mounts, &continuation_params)
            .expect("cursor is non-draining");
        assert_eq!(continued.entries, repeated.entries);
        assert!(fixture.resources.release_cursor(&cursor));
        assert!(fixture.resources.release_cursor(&cursor));

        let found = fixture
            .resources
            .find(
                &fixture.mounts,
                &FileFindParams {
                    context: context("find"),
                    root: path("/"),
                    pattern: "docs/*.txt".to_owned(),
                    mode: FindMode::Glob,
                    kind: Some(FileKind::File),
                    max_depth: 2,
                    cursor: None,
                    output_policy: None,
                },
            )
            .expect("find succeeds");
        assert_eq!(found.entries.len(), 9);

        let searched = fixture
            .resources
            .search(
                &fixture.mounts,
                &FileSearchParams {
                    context: context("search"),
                    root: path("/docs"),
                    query: "needle".to_owned(),
                    mode: SearchMode::Literal,
                    include: vec!["*.txt".to_owned()],
                    exclude: vec!["main.txt".to_owned()],
                    max_depth: 1,
                    cursor: None,
                    output_policy: None,
                    case_sensitive: true,
                },
            )
            .expect("search succeeds");
        assert_eq!(searched.matches.len(), 8);
        assert!(
            searched
                .matches
                .windows(2)
                .all(|pair| pair[0].path.path <= pair[1].path.path)
        );
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    #[test]
    fn mutations_preserve_preconditions_and_publish_complete_candidates() {
        let fixture = Fixture::new();
        fixture
            .resources
            .mkdir(
                &fixture.mounts,
                &FileMkdirParams {
                    context: context("mkdir"),
                    path: path("/work/nested"),
                    parents: true,
                    exist_ok: false,
                },
            )
            .expect("mkdir succeeds");
        let (created, bytes) = fixture
            .resources
            .write_text(
                &fixture.mounts,
                &FileWriteTextParams {
                    context: context("write"),
                    path: path("/work/nested/source.txt"),
                    mode: FileWriteMode::Create,
                    text: "hello world\n".to_owned(),
                    expected_revision: None,
                    executable: Some(false),
                },
            )
            .expect("create succeeds");
        assert_eq!(bytes, 12);
        let revision = created.revision.expect("revision");
        let (appended, _) = fixture
            .resources
            .write_text(
                &fixture.mounts,
                &FileWriteTextParams {
                    context: EIPCallContext {
                        idempotency_key: Some("append-key".to_owned()),
                        ..context("append")
                    },
                    path: path("/work/nested/source.txt"),
                    mode: FileWriteMode::Append,
                    text: "tail\n".to_owned(),
                    expected_revision: Some(revision),
                    executable: None,
                },
            )
            .expect("append uses atomic replacement");
        let patched_revision = appended.revision.expect("append revision");
        fixture
            .resources
            .patch_text(
                &fixture.mounts,
                &FilePatchTextParams {
                    context: context("patch"),
                    path: path("/work/nested/source.txt"),
                    patch_format: "unified_diff".to_owned(),
                    patch: "@@ -1,2 +1,2 @@\n-hello world\n+hello block2\n tail\n".to_owned(),
                    expected_revision: patched_revision,
                },
            )
            .expect("patch succeeds");
        assert_eq!(
            fs::read_to_string(fixture.native.join("work/nested/source.txt"))
                .expect("reads patched file"),
            "hello block2\ntail\n"
        );

        fixture
            .resources
            .copy(
                &fixture.mounts,
                &FileCopyParams {
                    context: context("copy"),
                    source: path("/work/nested/source.txt"),
                    destination: path("/work/nested/copy.txt"),
                    expected_source_revision: None,
                    expected_destination_revision: None,
                    replace: false,
                    require_atomic_destination: true,
                    require_stable_source: true,
                },
            )
            .expect("atomic copy succeeds");
        let moved = fixture
            .resources
            .move_path(
                &fixture.mounts,
                &FileMoveParams {
                    context: context("move"),
                    source: path("/work/nested/copy.txt"),
                    destination: path("/work/moved.txt"),
                    expected_source_revision: None,
                    expected_destination_revision: None,
                    replace: false,
                },
            )
            .expect("atomic move succeeds");
        fixture
            .resources
            .remove(
                &fixture.mounts,
                &FileRemoveParams {
                    context: context("remove"),
                    path: moved.path,
                    expected_kind: FileKind::File,
                    expected_revision: moved.revision,
                    recursive: false,
                    max_entries: 1,
                },
            )
            .expect("revision-checked removal succeeds");
        assert!(!fixture.native.join("work/moved.txt").exists());
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    #[test]
    fn rejects_unbounded_search_lines_and_preflights_recursive_remove() {
        let fixture = Fixture::new();
        fs::write(
            fixture.native.join("long.txt"),
            vec![b'a'; super::MAX_SEARCH_LINE_BYTES + 1],
        )
        .expect("long-line fixture");
        let searched = fixture.resources.search(
            &fixture.mounts,
            &FileSearchParams {
                context: context("long-search"),
                root: path("/"),
                query: "a".to_owned(),
                mode: SearchMode::Literal,
                include: Vec::new(),
                exclude: Vec::new(),
                max_depth: 1,
                cursor: None,
                output_policy: None,
                case_sensitive: true,
            },
        );
        assert!(matches!(searched, Err(ResourceError::Limit)));

        fs::create_dir_all(fixture.native.join("tree/child")).expect("remove tree");
        fs::write(fixture.native.join("tree/child/file"), b"data").expect("remove file");
        let bounded = fixture.resources.remove(
            &fixture.mounts,
            &FileRemoveParams {
                context: context("remove-bounded"),
                path: path("/tree"),
                expected_kind: FileKind::Directory,
                expected_revision: None,
                recursive: true,
                max_entries: 2,
            },
        );
        assert!(matches!(bounded, Err(ResourceError::Limit)));
        assert!(fixture.native.join("tree/child/file").exists());

        let removed = fixture
            .resources
            .remove(
                &fixture.mounts,
                &FileRemoveParams {
                    context: context("remove-complete"),
                    path: path("/tree"),
                    expected_kind: FileKind::Directory,
                    expected_revision: None,
                    recursive: true,
                    max_entries: 3,
                },
            )
            .expect("bounded recursive remove succeeds");
        assert_eq!(removed, 3);
        assert!(!fixture.native.join("tree").exists());
    }

    #[test]
    fn validates_patch_counts_and_no_newline_markers() {
        assert!(matches!(
            apply_unified_diff("one\ntwo\n", "@@ -1,1 +1,1 @@\n one\n-two\n+three\n"),
            Err(ResourceError::Invalid)
        ));
        assert!(matches!(
            apply_unified_diff("one\ntwo\n", "@@ -2 +9 @@\n-two\n+three\n"),
            Err(ResourceError::Invalid)
        ));
        let patch =
            "@@ -1 +1 @@\n-old\n\\ No newline at end of file\n+new\n\\ No newline at end of file\n";
        let (result, hunks) = apply_unified_diff("old", patch).expect("no-newline patch applies");
        assert_eq!(result, "new");
        assert_eq!(hunks, 1);
    }

    #[cfg(any(target_os = "linux", target_os = "macos"))]
    #[test]
    fn staged_replacement_refuses_symlink_leaf_but_patch_follows_contained_target() {
        use std::os::unix::fs::symlink;

        let fixture = Fixture::new();
        fs::write(fixture.native.join("target.txt"), b"original").expect("target fixture");
        symlink("target.txt", fixture.native.join("link.txt")).expect("symlink fixture");
        let result = fixture.resources.write_text(
            &fixture.mounts,
            &FileWriteTextParams {
                context: context("symlink-write"),
                path: path("/link.txt"),
                mode: FileWriteMode::Replace,
                text: "replacement".to_owned(),
                expected_revision: None,
                executable: None,
            },
        );
        assert!(matches!(result, Err(ResourceError::Denied)));
        assert_eq!(
            fs::read_to_string(fixture.native.join("target.txt")).expect("target remains"),
            "original"
        );

        let revision = fixture
            .resources
            .stat(
                &fixture.mounts,
                &FileStatParams {
                    context: context("symlink-stat"),
                    path: path("/link.txt"),
                    follow_symlinks: true,
                },
            )
            .expect("stats contained symlink target")
            .info
            .revision
            .expect("target revision");
        let patch = "@@ -1 +1 @@\n-original\n\\ No newline at end of file\n+patched\n\\ No newline at end of file\n";
        let (info, hunks) = fixture
            .resources
            .patch_text(
                &fixture.mounts,
                &FilePatchTextParams {
                    context: context("symlink-patch"),
                    path: path("/link.txt"),
                    patch_format: "unified_diff".to_owned(),
                    patch: patch.to_owned(),
                    expected_revision: revision,
                },
            )
            .expect("patches contained symlink target");
        assert_eq!(info.path, path("/link.txt"));
        assert_eq!(hunks, 1);
        assert!(
            fs::symlink_metadata(fixture.native.join("link.txt"))
                .expect("link remains")
                .file_type()
                .is_symlink()
        );
        assert_eq!(
            fs::read_to_string(fixture.native.join("target.txt")).expect("patched target"),
            "patched"
        );
    }
}
