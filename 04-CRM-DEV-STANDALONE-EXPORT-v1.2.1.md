# 獨立可執行系統匯出交付 v1.2.1

日期：2026-11-23（Asia/Taipei）。角色：DEVELOPMENT。授權：Owner 本輪「要有按鈕匯出  \"獨立可執行系統\"」。

本輪新增系統程式包匯出，不擴張為客戶資料匯出、備份、匯入、其他業務模組或下一階段。原需求、設計及 Review 文件保留原版本。本交付是 Development 驗證結果，不代替獨立程式 Review 或 Owner 驗收。

## 實際操作與範圍

具「帳號管理」授權的員工登入後，右上角可按「匯出獨立可執行系統」。頁面說明版本、平台、內容範圍，提供下載及取消；失敗訊息在控制項旁。一般員工、僅具全域編輯權者或未登入者不能直接取得下載。

匯出為 Windows x64 ZIP，完整解壓縮後雙擊 `CRM.exe`。不需要在目標電腦安裝 Python、Django 或開發工具。首次於啟動視窗設定自己的個人帳密，之後自動開啟本機瀏覽器。啟動視窗需保留，關閉後系統停止。

ZIP 僅含四項：

1. `CRM/CRM.exe`：含 Python 執行時、Django、SQLite、Waitress、應用、遷移及頁面資源的單一 EXE。
2. `CRM/使用說明.txt`。
3. `CRM/THIRD-PARTY-NOTICES.txt`。
4. `CRM/release.json`：版本、平台、EXE 大小及 SHA-256，不含資料或帳號。

這是全新系統，不複製目前帳號、密碼、客戶／業務紀錄、操作歷程、資料庫、密鑰、對話歷史、附件或 QA 資料。獨立版首次於 EXE 旁的 `data` 建立自己的 DB 與密鑰，重啟繼續使用。獨立版內亦可用同一按鈕再次匯出乾淨程式包，自己的 data 不隨匯出包帶出。

匯出不更動既有帳號、權限與業務紀錄；依歸責規則新增實際匯出操作歷程。沿用 R16 的 Employee→OperationEvent 關係，不新增業務實體或 FK；技術操作目標為固定命名的 system 身分，after 僅記版本、程式指紋及不含業務資料的標記。權限在開始時及產生檔案後的交易內重查，避免產製期間已撤權仍取得下載。

## 程式與建置

| 檔案 | 內容 |
| --- | --- |
| `crm/packaging.py`、`crm/release.py` | EXE 版本／完整性檢查、四檔白名單 ZIP、串流暫存及版本 |
| `crm/views.py`、`config/urls.py` | 受保護下載頁及 POST、CSRF、目前權限重查、可歸責且可重試的匯出事件 |
| `templates/base.html`、`templates/export.html` | 右上角按鈕、範圍說明、下載／取消與鄰近錯誤 |
| `portable_main.py` | 內建 Waitress、本機獨占 socket、首次初始化、預設埠無法使用時改選可用埠、UTF-8 輸出 |
| `config/settings.py` | 冷凍程式資源與可寫資料路徑分離；獨立版依 data 路徑使用隔離 cookie；來源模式維持既有資料位置 |
| `tools/CRM.spec`、`tools/build_portable.py` | PyInstaller 單檔封裝、乾淨 PATH、來源白名單及私有檔案排除檢查 |
| `portable/` | 使用說明與第三方授權 |
| `requirements.txt`、`requirements-build.txt` | 執行與建置套件分開鎖定；Waitress 3.0.2、PyInstaller 6.22.3 |
| `crm/tests/test_packaging.py`、`qa/portable_acceptance.py` | 下載權限、資料排除、串流、桌面、真正 EXE、重啟與再次匯出驗證 |
| `README.md`、`.gitignore` | 操作／重建說明及產物排除 |

建置採 PyInstaller onefile；程式資源從冷凍包內讀取，資料則位於持久化 data，避免寫入每次解壓縮的暫存資源目錄。[PyInstaller 執行時資源說明](https://pyinstaller.org/en/stable/runtime-information.html)

Windows 的 SO_REUSEADDR 可能讓同一使用者的兩個程式綁定同一埠。本輪已在實際共存檢查重現並修正：先以 SO_EXCLUSIVEADDRUSE 取得獨占 socket，再交給 Waitress，預設 18040 已用時自動另選埠，沒有關閉或接管另一套系統。[Microsoft socket 說明](https://learn.microsoft.com/en-us/windows/win32/winsock/using-so-reuseaddr-and-so-exclusiveaddruse)

封裝前排除開發環境 PATH 額外注入的 DLL；資料來源白名單僅接受程式資源、必要套件、Python／Windows 執行元件及產生的標準函式庫，拒絕資料庫與密鑰。Django hook 可能搜尋 manage.py 附近的 DB，故另有 spec 閘門及最終 TOC 檢查，不能直接壓縮整個專案。

## 真實驗證

### 應用與匯出測試

`.\.venv\Scripts\python.exe manage.py test crm.tests --noinput -v 1`

**PASS：39 項，38.937 秒。**其中 30 項為直接受共同設定影響的既有驗證，9 項為本次匯出與啟動檢查：白名單、資料排除、按鈕／取消、重複下載單一歸責事件、缺檔／竄改近端失敗、一般／全域編輯／匿名拒絕、CSRF、任意路徑輸入拒絕、產製期間撤權、產製中檔案變更、既有業務值保護及選埠。項目涵蓋可重疊，不把情境列數另累加成測試總數。

串流測試使用 TransactionTestCase，依實際請求完成時關閉連線的生命週期驗證；沒有放寬原權限或資料保護斷言。Django `check` 與遷移漂移檢查均 PASS，沒有新增業務 schema 或執行 Owner 資料遷移。

### 實際下載與獨立 EXE

`python qa/portable_acceptance.py` 的 **7 組端到端檢查 PASS**，證據為 `.qa/portable-21fed5a742/result.json`：

- 登入後的真實匯出按鈕、內容說明與取消。
- 瀏覽器真正下載 ZIP，僅四個白名單項目，EXE 指紋相符。
- 另一位一般員工無按鈕，直接請求 403。
- 將下載檔解壓到含中文／空格的獨立 QA 資料夾，啟動工作目錄不是原始碼目錄；PATH 僅留 Windows System32，移除 Python 相關環境。EXE 自行初始化空白 DB／新密鑰，未沿用刻意注入的來源 CRM_DB。
- EXE 本身提供登入、模板、靜態資源及個別認證。
- 透過 EXE 畫面儲存資料，真正停止及重啟程序後，資料與該 data 專屬登入 session 保留。
- 從該 EXE 內再次下載，程式指紋與原版相同，執行期間產生的 data 不被帶出。

最終 1.2.1 EXE 另於互動終端實際設定測試帳密：一位員工、三項獨立能力、一次可歸責初始化，沒有新增業務紀錄。原系統同時佔用 18040，獨立版未指定 port，實際選用 13241；兩者均回應 200，另一次 SO_REUSEADDR 綁定獨立版的埠被拒絕。證據位於 `.qa/portable-121-first-9555cf77/result.json`。測試帳密僅限該隔離目錄，未放入交付包或 Owner 資料。

### 既有資料與目前系統

目前本機服務已重新載入新功能，網址仍為 `http://127.0.0.1:18040/`；只停止／重啟已核實屬於本專案的程序。Owner 既有帳號、授權、業務值及既有歷程指紋與更新前相符；更新後出現的新增事件是合法匯出操作，沒有把預期新增歸責記錄誤判為覆寫。證據 `.qa/export-owner-preservation.json`。

原需求、設計及 AGENTS.md 指紋保持不變。所有 QA 程序只處理自己的唯一資料目錄，已停止；目前 Owner 本機服務保留執行。不更動 Windows 排除埠、防火牆或其他全域設定。沒有 Git 操作。

## 交付與限制

交付：`dist/portable/CRM.exe`、`dist/portable/CRM-Windows-x64.zip` 及 `release.json`。SHA-256 與完整原始碼清單見 `04-CRM-DEV-PHASE1-IMPLEMENTATION-MANIFEST-v1.2.1.json`。建置記錄：`.qa/portable-build-1.2.1.log`。必要桌面證據僅留私有 QA 目錄，不貼入對話歷史。

本輪於 Windows 11 x64 驗證；沒有第二台實體電腦或乾淨虛擬機測試，也未建立 macOS／Linux 版本。移除 Python 路徑、隔離資料與實際冷凍 EXE 的檢查不冒稱為在另一台電腦執行。

Development 實作與上述驗證 PASS；獨立程式 Review 尚未執行，Owner 功能驗收未代為宣告。沒有已知未解錯誤。本輪沒有待 Owner 回答的問題。
