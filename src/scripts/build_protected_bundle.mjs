#!/usr/bin/env node

import { promises as fs } from "fs";
import path from "path";
import process from "process";
import { webcrypto } from "crypto";

const { subtle } = webcrypto;
const encoder = new TextEncoder();

const DEFAULT_APP_HTML = "src/nanogram.app.html";
const DEFAULT_BUNDLE_OUT = "build/public/site.bundle.json";
const DEFAULT_INCLUDES = ["src/themes/default/css/nanogram.css", "build/assets"];
const DEFAULT_ITERATIONS = 250000;
const DEFAULT_OBJS_DIR = "build/public/objs";
const DEFAULT_LAZY_MAX_BYTES = 15728640;
const EAGER_CIPHERS_FILE_NAME = "eager-ciphers.json";

function usage() {
  console.log(
    [
      "Usage:",
      "  node src/scripts/build_protected_bundle.mjs --password <password> [options]",
      "",
      "Options:",
      `  --app-html <path>         App HTML to encrypt (default: ${DEFAULT_APP_HTML})`,
      `  --bundle-out <path>       Output encrypted bundle path (default: ${DEFAULT_BUNDLE_OUT})`,
      `  --objs-dir <path>         Output directory for per-file encrypted JSON objects (default: ${DEFAULT_OBJS_DIR})`,
      `  --iterations <n>          PBKDF2 iterations (default: ${DEFAULT_ITERATIONS})`,
      `  --lazy-max-bytes <n>      Lazy-load media bigger than n plaintext bytes (default: ${DEFAULT_LAZY_MAX_BYTES})`,
      "  --include <path>          Extra file/dir to include (repeatable)",
      "  --no-default-includes     Do not auto-include src/themes/default/css/nanogram.css and build/assets",
      "  -h, --help                Show this help",
    ].join("\n")
  );
}

function toPosixRelative(targetPath) {
  const resolved = path.resolve(targetPath);
  const relative = path.relative(process.cwd(), resolved);
  return relative.split(path.sep).join("/");
}

function base64FromBytes(bytes) {
  return Buffer.from(bytes).toString("base64");
}

function hexFromArrayBuffer(buffer) {
  return Array.from(new Uint8Array(buffer))
    .map((n) => n.toString(16).padStart(2, "0"))
    .join("");
}

async function sha256Hex(text) {
  return hexFromArrayBuffer(await subtle.digest("SHA-256", encoder.encode(text)));
}

async function sha256Bytes(bytes) {
  return new Uint8Array(await subtle.digest("SHA-256", bytes));
}

async function deriveDeterministicSalt(password, iterations) {
  const material = encoder.encode(`nanogram/salt/v1\0${password}\0${iterations}`);
  const digest = await sha256Bytes(material);
  return digest.slice(0, 16);
}

async function deriveDeterministicIv(relPath, plainBytes) {
  const plainHash = await sha256Bytes(plainBytes);
  const prefix = encoder.encode(`nanogram/iv/v1\0${relPath}\0`);
  const input = new Uint8Array(prefix.length + plainHash.length);
  input.set(prefix, 0);
  input.set(plainHash, prefix.length);
  const digest = await sha256Bytes(input);
  return digest.slice(0, 12);
}

function parseArgs(argv) {
  const args = {
    password: "",
    appHtml: DEFAULT_APP_HTML,
    bundleOut: DEFAULT_BUNDLE_OUT,
    objsDir: DEFAULT_OBJS_DIR,
    iterations: DEFAULT_ITERATIONS,
    lazyMaxBytes: DEFAULT_LAZY_MAX_BYTES,
    includes: [],
    useDefaultIncludes: true,
  };

  for (let i = 0; i < argv.length; i += 1) {
    const token = argv[i];
    if (token === "-h" || token === "--help") {
      usage();
      process.exit(0);
    }
    if (token === "--password") {
      args.password = argv[i + 1] || "";
      i += 1;
      continue;
    }
    if (token === "--app-html") {
      args.appHtml = argv[i + 1] || "";
      i += 1;
      continue;
    }
    if (token === "--bundle-out") {
      args.bundleOut = argv[i + 1] || "";
      i += 1;
      continue;
    }
    if (token === "--objs-dir" || token === "--ciphertexts-dir") {
      args.objsDir = argv[i + 1] || "";
      i += 1;
      continue;
    }
    if (token === "--iterations") {
      const parsed = Number(argv[i + 1]);
      if (!Number.isFinite(parsed) || parsed < 1000) {
        throw new Error("--iterations must be a number >= 1000");
      }
      args.iterations = Math.floor(parsed);
      i += 1;
      continue;
    }
    if (token === "--lazy-max-bytes") {
      const parsed = Number(argv[i + 1]);
      if (!Number.isFinite(parsed) || parsed < 0) {
        throw new Error("--lazy-max-bytes must be a number >= 0");
      }
      args.lazyMaxBytes = Math.floor(parsed);
      i += 1;
      continue;
    }
    if (token === "--include") {
      const value = argv[i + 1] || "";
      if (!value) {
        throw new Error("--include requires a path");
      }
      args.includes.push(value);
      i += 1;
      continue;
    }
    if (token === "--no-default-includes") {
      args.useDefaultIncludes = false;
      continue;
    }
    throw new Error(`Unknown option: ${token}`);
  }

  if (!args.password) {
    throw new Error("--password is required");
  }

  if (!args.appHtml) {
    throw new Error("--app-html is required");
  }

  if (!args.bundleOut) {
    throw new Error("--bundle-out is required");
  }

  if (!args.objsDir) {
    throw new Error("--objs-dir is required");
  }

  return args;
}

async function pathExists(p) {
  try {
    await fs.access(p);
    return true;
  } catch {
    return false;
  }
}

async function walkFiles(entryPath, outSet) {
  const stat = await fs.stat(entryPath);
  if (stat.isFile()) {
    outSet.add(toPosixRelative(entryPath));
    return;
  }
  if (!stat.isDirectory()) {
    return;
  }

  const dirEntries = await fs.readdir(entryPath, { withFileTypes: true });
  for (const dirent of dirEntries) {
    await walkFiles(path.join(entryPath, dirent.name), outSet);
  }
}

function mimeForPath(filePath) {
  const ext = path.extname(filePath).toLowerCase();
  if (ext === ".html") return "text/html";
  if (ext === ".css") return "text/css";
  if (ext === ".js" || ext === ".mjs") return "application/javascript";
  if (ext === ".json") return "application/json";
  if (ext === ".svg") return "image/svg+xml";
  if (ext === ".png") return "image/png";
  if (ext === ".jpg" || ext === ".jpeg") return "image/jpeg";
  if (ext === ".webp") return "image/webp";
  if (ext === ".gif") return "image/gif";
  if (ext === ".mp4") return "video/mp4";
  if (ext === ".mov") return "video/quicktime";
  if (ext === ".webm") return "video/webm";
  return "application/octet-stream";
}

async function deriveEncryptionKey(password, salt, iterations) {
  const keyMaterial = await subtle.importKey(
    "raw",
    encoder.encode(password),
    { name: "PBKDF2" },
    false,
    ["deriveKey"]
  );
  return subtle.deriveKey(
    {
      name: "PBKDF2",
      hash: "SHA-256",
      salt,
      iterations,
    },
    keyMaterial,
    { name: "AES-GCM", length: 256 },
    false,
    ["encrypt"]
  );
}

function isLazyEligibleMedia(relPath, mime) {
  if (typeof mime !== "string" || !(mime.startsWith("video/") || mime.startsWith("image/"))) {
    return false;
  }
  const normalized = toPosixRelative(relPath);
  return /(^|\/)assets\/(posts|reels)\//.test(normalized);
}

async function main() {
  const args = parseArgs(process.argv.slice(2));
  const appHtmlRel = toPosixRelative(args.appHtml);
  const bundleOutRel = toPosixRelative(args.bundleOut);
  const bundleOutAbs = path.resolve(args.bundleOut);
  const objsDirRel = toPosixRelative(args.objsDir);
  const objsDirAbs = path.resolve(args.objsDir);
  const bundleDirRel = path.posix.dirname(bundleOutRel);
  const eagerCipherFileAbs = path.join(objsDirAbs, EAGER_CIPHERS_FILE_NAME);
  const eagerCipherFileRel = toPosixRelative(eagerCipherFileAbs);
  const eagerCipherFileClientRel =
    path.posix.relative(bundleDirRel, eagerCipherFileRel) || EAGER_CIPHERS_FILE_NAME;

  const includeRoots = [];
  includeRoots.push(args.appHtml);
  if (args.useDefaultIncludes) {
    includeRoots.push(...DEFAULT_INCLUDES);
  }
  includeRoots.push(...args.includes);

  const fileSet = new Set();
  for (const root of includeRoots) {
    const abs = path.resolve(root);
    if (!(await pathExists(abs))) {
      console.warn(`Skipping missing path: ${root}`);
      continue;
    }
    await walkFiles(abs, fileSet);
  }

  fileSet.delete(bundleOutRel);
  fileSet.delete(toPosixRelative(`${bundleOutAbs}.tmp`));
  for (const candidate of Array.from(fileSet)) {
    if (candidate === objsDirRel || candidate.startsWith(`${objsDirRel}/`)) {
      fileSet.delete(candidate);
    }
  }

  if (!fileSet.has(appHtmlRel)) {
    throw new Error(`App HTML file was not included: ${appHtmlRel}`);
  }

  const sortedFiles = Array.from(fileSet).sort();
  if (!sortedFiles.length) {
    throw new Error("No files to encrypt");
  }

  const passwordHashHex = hexFromArrayBuffer(
    await subtle.digest("SHA-256", encoder.encode(args.password))
  );
  const salt = await deriveDeterministicSalt(passwordHashHex, args.iterations);
  const key = await deriveEncryptionKey(passwordHashHex, salt, args.iterations);

  let encryptedCount = 0;
  let lazyCount = 0;
  let eagerCount = 0;
  let lazyPlainBytes = 0;
  let totalPlainBytes = 0;
  const bundleMeta = {
    version: 1,
    created_at_utc: "1970-01-01T00:00:00.000Z",
    app_html_path: appHtmlRel,
    password_hash_sha256: passwordHashHex,
    kdf: {
      algorithm: "PBKDF2",
      hash: "SHA-256",
      password_input: "sha256_hex",
      iterations: args.iterations,
      salt_b64: base64FromBytes(salt),
    },
    cipher: {
      algorithm: "AES-GCM",
      iv_bytes: 12,
      tag_bytes: 16,
    },
    storage: {
      ciphertext_container: "json_blob_for_eager_and_json_per_file_for_lazy",
      objs_dir: path.posix.relative(bundleDirRel, objsDirRel) || ".",
      ciphertext_field: "ciphertext_b64",
      entry_field: "ciphertext_json",
      eager_entry_field: "ciphertext_name",
      eager_ciphertexts_json: eagerCipherFileClientRel,
      eager_item_name_field: "name",
      eager_item_cipher_field: "cipher",
    },
    lazy_load: {
      enabled: args.lazyMaxBytes > 0,
      max_plaintext_bytes: args.lazyMaxBytes,
      mode: "media_only",
    }
  };

  await fs.mkdir(path.dirname(bundleOutAbs), { recursive: true });
  await fs.mkdir(objsDirAbs, { recursive: true });
  const tmpBundleOutAbs = `${bundleOutAbs}.tmp`;
  const bundleHandle = await fs.open(tmpBundleOutAbs, "w");
  try {
    const metaJson = JSON.stringify(bundleMeta);
    await bundleHandle.write(`${metaJson.slice(0, -1)},"files":[`);

    const makeObjFileName = async (relPath) => `${await sha256Hex(relPath)}.json`;
    const eagerCipherItems = [];

    for (let i = 0; i < sortedFiles.length; i += 1) {
      const relPath = sortedFiles[i];
      const absPath = path.resolve(relPath);
      const plain = await fs.readFile(absPath);
      const plainBytes = plain.byteLength;
      totalPlainBytes += plainBytes;
      const mime = mimeForPath(relPath);
      const shouldLazyLoad = args.lazyMaxBytes > 0
        && plainBytes > args.lazyMaxBytes
        && isLazyEligibleMedia(relPath, mime);
      if (shouldLazyLoad) {
        lazyCount += 1;
        lazyPlainBytes += plainBytes;
      } else {
        eagerCount += 1;
      }

      const iv = await deriveDeterministicIv(relPath, plain);
      const encrypted = await subtle.encrypt(
        { name: "AES-GCM", iv },
        key,
        plain
      );

      const entry = {
        path: relPath,
        mime,
        iv_b64: base64FromBytes(iv),
        plain_bytes: plainBytes,
        lazy: shouldLazyLoad,
      };
      const ciphertextFileName = await makeObjFileName(relPath);
      const ciphertextFileAbs = path.join(objsDirAbs, ciphertextFileName);
      const ciphertextFileRel = toPosixRelative(ciphertextFileAbs);
      const ciphertextFileClientRel = path.posix.relative(bundleDirRel, ciphertextFileRel) || ciphertextFileName;
      const encryptedBytes = new Uint8Array(encrypted);
      const encryptedBase64 = base64FromBytes(encryptedBytes);
      if (shouldLazyLoad) {
        const ciphertextPayload = {
          ciphertext_b64: encryptedBase64,
        };
        await fs.writeFile(
          ciphertextFileAbs,
          JSON.stringify(ciphertextPayload),
          "utf8"
        );
        entry.ciphertext_json = ciphertextFileClientRel;
      } else {
        const eagerCipherName = `obj/${ciphertextFileName}`;
        eagerCipherItems.push({
          name: eagerCipherName,
          cipher: encryptedBase64,
        });
        entry.ciphertext_name = eagerCipherName;
      }
      entry.ciphertext_bytes = encryptedBytes.byteLength;

      if (i > 0) {
        await bundleHandle.write(",");
      }
      await bundleHandle.write(JSON.stringify(entry));
      encryptedCount += 1;
    }

    const eagerCipherPayload = {
      files: eagerCipherItems,
    };
    await fs.writeFile(
      eagerCipherFileAbs,
      JSON.stringify(eagerCipherPayload),
      "utf8"
    );

    await bundleHandle.write("]}");
  } finally {
    await bundleHandle.close();
  }
  await fs.rename(tmpBundleOutAbs, bundleOutAbs);

  console.log(`Encrypted files: ${encryptedCount}`);
  console.log(`Total plaintext bytes: ${totalPlainBytes}`);
  console.log(`Eager entries: ${eagerCount}`);
  console.log(`Lazy entries: ${lazyCount}`);
  console.log(`Lazy plaintext bytes: ${lazyPlainBytes}`);
  console.log(`Bundle: ${bundleOutRel}`);
  console.log(`Encrypted objects dir: ${objsDirRel}`);
  console.log(`Eager ciphertext blob: ${eagerCipherFileClientRel}`);
  console.log(`App HTML path in bundle: ${appHtmlRel}`);
}

main().catch((error) => {
  console.error(`Error: ${error instanceof Error ? error.message : String(error)}`);
  process.exit(1);
});
