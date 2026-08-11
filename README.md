# MemoPen

MemoPen 是一個 PySide6 製作的輕量隨手記工具，主打快速輸入、中文輸入法相容、固定 14pt 文字區、項目編碼與自動儲存。

## 最新版本

- 目前主程式：`MemoPen_V0.14.1.py`
- Windows 無主控台版本：`MemoPen_V0.14.1.pyw`
- Nuitka 建置批次檔：`build_MemoPen_V0.14.1_Nuitka.bat`

## 執行方式

```bash
python -m pip install -r requirements.txt
python MemoPen_V0.14.1.py
```

Windows 可直接以 Python Launcher 執行：

```bat
py -m pip install -r requirements.txt
py MemoPen_V0.14.1.py
```

## V0.14.1 更新

- 開啟舊筆記前會先儲存目前內容，降低未儲存文字被覆蓋的風險。
- 關閉視窗時若儲存失敗會保留視窗，不會靜默結束。
- 選擇新儲存資料夾失敗時會還原原本檔案路徑。
- 強化文字檔讀取與 UTF-8 原子寫入錯誤訊息。
- 同步 `.py`、`.pyw`、Nuitka 批次檔與轉譯說明版本號。

## Nuitka 打包

Windows 上可執行：

```bat
build_MemoPen_V0.14.1_Nuitka.bat
```

完整說明請見 `MemoPen_V0.14.1_Nuitka_轉譯說明.txt`。
