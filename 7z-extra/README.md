# 7z-extra

这个目录里是 **7-Zip 官方 extra 包**里的独立解压内核 `x64\7za.exe`，
`build_installer.ps1` 打包安装包时要用它来解压载荷。

- 来源：<https://www.7-zip.org/download.html> 的 “7-Zip Extra” 包
- 许可：7-Zip 的许可（LGPL + unRAR 限制），本仓库**原样分发未修改的官方二进制**，
  仅用于解压自己打包的载荷
- 如果不想把二进制放进版本库，可以把它从 `.gitignore` 之外删掉，改用：

  ```powershell
  # 下载 7-Zip Extra 后解压，把 x64\7za.exe 放到这里
  # 目录结构应为： 7z-extra\x64\7za.exe
  ```

  缺少它时 `build_installer.ps1` 会直接报错并说明路径。
