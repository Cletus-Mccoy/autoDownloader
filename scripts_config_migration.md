# Migration Plan: Centralized JSON Config for app/scripts/

This document details the migration of configuration variables from each script in `app/scripts/` to a shared JSON config file. Dynamic paths (built from other variables or environment) are noted and should be handled in code, not in the static config file.

---

## 1. download.py
| Variable Name    | Current Value/Type                | Migrate to JSON Config? | JSON Key           | Notes on Migration / Handling Dynamic Paths                |
|------------------|-----------------------------------|------------------------|--------------------|------------------------------------------------------------|
| DATA_DIR         | "/app/data" (str)                 | No                     | -                  | Dynamic, used as base for other paths. Set in code/env.    |
| DOWNLOAD_DIR     | f"{DATA_DIR}/downloads" (str)     | No                     | -                  | Built from DATA_DIR. Set in code using DATA_DIR.           |
| LOG_DIR          | f"{DATA_DIR}/logs" (str)          | No                     | -                  | Built from DATA_DIR. Set in code using DATA_DIR.           |
| MUSIC_EXTENSIONS | {".mp3", ...} (set of str)        | Yes                    | music_extensions   | Store as list in JSON.                                     |
| ...              | ...                               | ...                    | ...                | Review for other constants/configs.                        |

---

## 2. runner.py
| Variable Name    | Current Value/Type                | Migrate to JSON Config? | JSON Key           | Notes on Migration / Handling Dynamic Paths                |
|------------------|-----------------------------------|------------------------|--------------------|------------------------------------------------------------|
| LOG_DIR          | f"{DATA_DIR}/logs" (str)          | No                     | -                  | Built from DATA_DIR. Set in code using DATA_DIR.           |
| ...              | ...                               | ...                    | ...                | Review for other constants/configs.                        |

---

## 3. scheduler.py
| Variable Name    | Current Value/Type                | Migrate to JSON Config? | JSON Key           | Notes on Migration / Handling Dynamic Paths                |
|------------------|-----------------------------------|------------------------|--------------------|------------------------------------------------------------|
| CRON_FILE        | "/etc/cron.d/ytmusic" (str)       | Yes                    | cron_file          | Can be static in config, or overridden by env.             |
| CRON_SUFFIX      | "root python ..." (str)           | Yes                    | cron_suffix        | Static string, can be in config.                           |
| ...              | ...                               | ...                    | ...                | Review for other constants/configs.                        |

---

## 4. timer.py
| Variable Name    | Current Value/Type                | Migrate to JSON Config? | JSON Key           | Notes on Migration / Handling Dynamic Paths                |
|------------------|-----------------------------------|------------------------|--------------------|------------------------------------------------------------|
| Any timeouts, intervals, or limits | (int/float)         | Yes (if user-configurable) | timer_interval, etc. | Only if user should control these.                         |

---

## 5. ytmusic_auth.py
| Variable Name    | Current Value/Type                | Migrate to JSON Config? | JSON Key           | Notes on Migration / Handling Dynamic Paths                |
|------------------|-----------------------------------|------------------------|--------------------|------------------------------------------------------------|
| COOKIES_FILE     | f"{AUTH_DIR}/cookies.txt" (str)   | No                     | -                  | Built from AUTH_DIR. Set in code using AUTH_DIR.           |
| ...              | ...                               | ...                    | ...                | Review for other constants/configs.                        |

---

## 6. _init.py
| Variable Name    | Current Value/Type                | Migrate to JSON Config? | JSON Key           | Notes on Migration / Handling Dynamic Paths                |
|------------------|-----------------------------------|------------------------|--------------------|------------------------------------------------------------|
| (Usually none)   |                                   |                        |                    | Typically for package init, but review for any configs.    |

---

## General Notes
- Only static, user-configurable values go in `config.json`.
- Dynamic paths are constructed in code, with their base directory optionally settable via environment or command-line.
- All scripts should be refactored to use the new config for relevant values.

### Example config.json
```json
{
  "cron_file": "/etc/cron.d/ytmusic",
  "cron_suffix": "root python /app/scripts/scheduler.py >> /var/log/cron.log 2>&1",
  "music_extensions": [".mp3", ".m4a", ".flac", ".ogg", ".opus", ".wav", ".aac", ".wma"],
  "unsupported_titles": ["Liked Music", "Episodes for Later"],
  "timer_interval": 60
}
```

If you want a detailed breakdown for a specific script, see the table above or request further details.
