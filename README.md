# MemoPen

MemoPen 是一個為「快速記下眼前想法」設計的輕量隨手記工具。它使用 PySide6 / Qt 製作，介面保持乾淨、白底、無標題列，開啟後就能直接輸入，不需要先建立專案或設定檔案。

程式特別針對繁體中文輸入情境調整，支援中文輸入法組字、選字與全形符號，適合日常備忘、會議筆記、臨時清單、靈感紀錄與工作中快速暫存文字。

## 程式特色

- 白色極簡筆記視窗，輸入區固定 14pt，閱讀與輸入都清楚。
- 無標題列設計，工具列與狀態列空白處可直接拖曳視窗。
- 支援「最上層」模式，可把筆記浮在其他視窗上方。
- 支援項目編碼，可快速插入 `1、2、3、` 這類條列序號。
- 每 30 秒自動儲存為 UTF-8 `.txt`，降低筆記遺失風險。
- 新筆記會自動建立時間戳，檔名格式為 `Memo_YYYYMMDDhhmm.txt`。
- 可開啟舊 TXT 檔，並支援 UTF-8、UTF-8 BOM、Big5、CP950 常見中文編碼。
- 提供「Q」按鈕快速更換儲存資料夾。
- 使用 `QSaveFile` 原子寫入，儲存過程更安全。
- 關閉前會再次儲存；若儲存失敗，視窗會保留並提示錯誤。
- 內建 Nuitka 專案設定，方便在 Windows 打包成無控制台單一 EXE。

## 最新版本

- 目前主程式：`MemoPen_V0.14.2.py`
- Windows 無主控台版本：`MemoPen_V0.14.2.pyw`
- Nuitka 建置批次檔：`build_MemoPen_V0.14.2_Nuitka.bat`

## 執行方式

```bash
python -m pip install -r requirements.txt
python MemoPen_V0.14.2.py
```

Windows 可直接以 Python Launcher 執行：

```bat
py -m pip install -r requirements.txt
py MemoPen_V0.14.2.py
```

## V0.14.2 更新

- 升級版本號至 `V0.14.2`。
- 同步更新 `.py`、`.pyw`、Nuitka 批次檔與轉譯說明檔名。
- GitHub 首頁說明改為以 MemoPen 程式特色為主。
- 維持 `V0.14.1` 的資料安全修正：開檔前儲存、關閉失敗保留視窗、UTF-8 原子寫入與讀檔錯誤提示。

## Nuitka 打包

Windows 上可執行：

```bat
build_MemoPen_V0.14.2_Nuitka.bat
```

完整說明請見 `MemoPen_V0.14.2_Nuitka_轉譯說明.txt`。
