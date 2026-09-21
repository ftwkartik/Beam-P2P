# ADR-006: Stream received data to the Origin Private File System

- **Status:** Accepted
- **Date:** 2026-08-06

## Context
Accumulating every chunk in memory and building a Blob at the end makes memory scale with file size, and large
transfers crash the tab. Resume across reloads also needs data to persist.

## Decision
The browser receiver writes frames at their offsets into **OPFS** files from a **dedicated worker** using
`FileSystemSyncAccessHandle` (synchronous, fast, and off the main thread). Transfer metadata and bitmaps go to
**IndexedDB**. On completion, the user saves via `showSaveFilePicker` / `showDirectoryPicker` where supported
(streamed copy), or via a download of the OPFS-backed `File` object (disk-backed, not RAM). Before accepting, the
receiver checks `navigator.storage.estimate()` and requests `persist()`. Stale partials are cleaned on start
(older than 7 days).

## Alternatives
- **In-memory Blob parts:** simple, but RAM-bound -- large transfers crash the tab.
- **File System Access API only (write directly to a user file):** ideal on Chromium, but unavailable in Firefox and Safari, and resume after a reload needs re-permission.
- **Service Worker streaming download (StreamSaver.js pattern):** no resume, and fragile across browsers.
- **IndexedDB blob chunks:** works everywhere, but slower and with high write amplification for GB-scale data.

## Trade-offs
- OPFS quota depends on the browser (usually a share of free disk), so the quota is checked up front.
- When the final save can't be written directly, it copies from OPFS (a second disk write). Acceptable.
- Older browsers without sync access handles fall back to in-memory for files under 200 MB, with a warning.

## Consequences
- Memory stays bounded regardless of file size. This is verified by the large-file memory test.
- The receiver can reload mid-transfer and resume.
