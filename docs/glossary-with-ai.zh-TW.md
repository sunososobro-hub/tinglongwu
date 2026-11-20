# 用你自己的 AI 做詞彙表

你這行的某些詞老是翻錯（產品術語、團隊黑話、縮寫）？可以自己修：請你平常在用的 AI（ChatGPT、Claude、Gemini…）幫你寫一份詞彙表檔案，丟進資料夾，聽龍下次啟動就會讀。不會上傳任何東西，也不是重新訓練模型，它只是一份留在你電腦上的純文字詞表。

English: [glossary-with-ai.md](glossary-with-ai.md)

## 怎麼做

1. **複製下面的提示詞**（英文那份，在 [glossary-with-ai.md](glossary-with-ai.md#the-prompt)），貼到 AI 的新對話。
2. **告訴它你的領域**、你聽的語言、你要的語言，以及（可選）翻錯的詞。例如：「我聽俄文的網通工程會議，要繁體中文。『частота』被翻成『乾淨』。」
3. **把它的回答存成 `.txt`**（UTF-8），例如 `my-terms.txt`，放進詞彙表資料夾：
   - Windows：`%APPDATA%\tinglongwu\glossary\`（貼到檔案總管網址列；沒有就自己建）
   - macOS：`~/Library/Application Support/tinglongwu/glossary/`
   - Linux：`~/.config/tinglongwu/glossary/`
4. **重開聽龍。**選單會顯示讀到幾個詞彙表檔案；你的詞優先於內建詞。

隱私：檔案不會離開你的電腦。但 AI 聊天是雲端服務，給它領域名稱和例詞就好，不要貼機密文件或會議內容。

## 分享

好的詞表能幫到同行。內建的領域檔放在 `src/core/tinglongwu/data/terms/`，歡迎開 PR 加一個檔案（格式見 CONTRIBUTING.md），或貼到 [Discussions](https://github.com/sunososobro-hub/tinglongwu/discussions)。
