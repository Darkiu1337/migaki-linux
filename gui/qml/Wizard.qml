import QtQuick
import QtQuick.Controls
import QtQuick.Dialogs
import QtQuick.Layouts

Dialog {
    id: root
    modal: true
    width: Math.min(760, root.parent ? root.parent.width - 48 : 760)
    height: Math.min(560, root.parent ? root.parent.height - 48 : 560)
    padding: 16
    anchors.centerIn: parent
    standardButtons: Dialog.NoButton
    property string gid: ""
    property var runnerKeys: ["proton", "rpgmaker", "native"]
    property var runnerDescs: []
    property var variantNames: []
    property var localeModel: []
    property string runner: "proton"
    // Remembered GPU pick so a late gpusChanged refresh can restore it.
    property string wantedGpu: ""
    // "ok" | "info" | "warn" — tints the detect label and reveals the switch.
    property string detectSeverity: "ok"

    ButtonGroup { id: runnerGroup }

    // The X11/display GPU is the only one that can present a filtered
    // Chromium (RPGMaker) window; tag it and warn on a mismatched pick.
    function gpuLabel(name) {
        return name + (isDisplayGpu(name) ? "  (display)" : "")
    }
    function isDisplayGpu(name) {
        var dv = backend.displayGpuVendor()
        return dv !== "" && backend.gpuVendor(name) === dv
    }
    function gpuMismatch() {
        if (root.runner !== "rpgmaker")
            return false
        var dv = backend.displayGpuVendor()
        if (dv === "")
            return false
        var v = backend.gpuVendor(gpuCombo.currentText)
        return v !== "" && v !== dv
    }

    function setRunner(k) {
        root.runner = k
        for (var i = 0; i < root.runnerKeys.length; i++)
            runnerRepeater.itemAt(i).checked = (root.runnerKeys[i] === k)
    }

    // A path was chosen (picker/paste). Keep the runner honest: a Windows .exe
    // under the native runner is corrected here.
    function pathChosen(p) {
        pathField.text = p
        backend.rememberDir(p)
        if (root.runner === "native")
            reconcileSelection("")
    }

    // Reconcile the current (path, runner): resolve the real launch target and
    // move a Windows .exe off the native runner. Drives Detect and path picks.
    function reconcileSelection(preface) {
        preface = preface || ""
        var r
        try { r = JSON.parse(backend.reconcileSelection(pathField.text.trim(), root.runner)) }
        catch (e) { r = null }
        if (!r) {
            if (preface !== "") detectLabel.text = preface
            return
        }
        if (r.runner !== root.runner)
            setRunner(r.runner)
        if (r.target !== "" && r.target !== pathField.text)
            pathField.text = r.target
        root.detectSeverity = r.severity
        if (r.message !== "" || preface !== "") {
            var line = r.message
            if (preface !== "") line = line === "" ? preface : (preface + " " + line)
            detectLabel.text = line
        }
    }

    Connections {
        target: backend
        function onGpusChanged() {
            gpuCombo.model = backend.listGpus()
            var i = root.wantedGpu === "" ? 0 : gpuCombo.find(root.wantedGpu)
            gpuCombo.currentIndex = i >= 0 ? i : 0
        }
    }

    function start(gid) {
        root.gid = gid
        root.title = gid === "" ? "Add game" : "Edit game"
        var descs = JSON.parse(backend.runnersJson())
        root.runnerDescs = root.runnerKeys.map(function(k) { return k + " — " + descs[k] })
        root.variantNames = backend.listVariants()
        variantCombo.model = root.variantNames.map(function(v) {
            var n = backend.variantNote(v)
            return n !== "" ? v + " — " + n : v
        })
        gpuCombo.model = backend.listGpus()
        root.localeModel = backend.locales()
        if (gid === "") {
            root.runner = "proton"
            runnerRepeater.itemAt(0).checked = true
            pathField.text = ""
            detectLabel.text = "Tip: Detect fills in the runner from the previous page."
            variantCombo.currentIndex = Math.max(0, root.variantNames.indexOf("L"))
            gpuCombo.currentIndex = 0
            root.wantedGpu = ""
            fpsSpin.value = 60
            hudCheck.checked = false
            langCombo.currentIndex = 0
            prefixCheck.checked = false
            nameField.text = ""
            trEnable.checked = false
            browserCheck.checked = false
            hookerCheck.checked = false
        } else {
            var g = JSON.parse(backend.gameData(gid))
            root.runner = g.runner || "proton"
            for (var i = 0; i < root.runnerKeys.length; i++)
                runnerRepeater.itemAt(i).checked = (root.runnerKeys[i] === root.runner)
            pathField.text = g.path || ""
            variantCombo.currentIndex = Math.max(0, root.variantNames.indexOf(g.variant || "L"))
            root.wantedGpu = g.gpu || ""
            var gi = gpuCombo.find(root.wantedGpu)
            gpuCombo.currentIndex = gi >= 0 ? gi : 0
            fpsSpin.value = g.fps === "off" ? 0 : (parseInt(g.fps) || 60)
            hudCheck.checked = g.hud === "1"
            // Keep a stored custom locale selectable (drop-down only).
            var locs = backend.locales()
            var lv = g.lang || ""
            if (lv !== "" && locs.indexOf(lv) < 0)
                locs.push(lv)
            root.localeModel = locs
            langCombo.currentIndex = Math.max(0, locs.indexOf(lv))
            prefixCheck.checked = g.prefix_mode === "game"
            nameField.text = g.name || ""
            var tr = g.translate || {}
            trEnable.checked = tr.enabled === "1"
            browserCheck.checked = tr.show_browser === "1"
            hookerCheck.checked = tr.show_hooker === "1"
        }
        errLabel.text = ""
        root.detectSeverity = "ok"
        pages.currentIndex = 0
        root.open()
    }

    function collect() {
        var lang = langCombo.currentIndex <= 0 ? "" : langCombo.currentText
        return JSON.stringify({
            name: nameField.text.trim(),
            runner: root.runner,
            path: pathField.text.trim(),
            variant: variantCombo.currentText.split(" ")[0],
            gpu: gpuCombo.currentText,
            fps: fpsSpin.value === 0 ? "off" : String(fpsSpin.value),
            hud: hudCheck.checked ? "1" : "0",
            lang: lang,
            prefix_mode: prefixCheck.checked ? "game" : "shared",
            translate: {
                enabled: (trEnable.checked && root.runner === "proton") ? "1" : "0",
                show_browser: browserCheck.checked ? "1" : "0",
                show_hooker: hookerCheck.checked ? "1" : "0"
            }
        })
    }

    function finish() {
        var data = collect()
        if (JSON.parse(data).name === "")
            data = JSON.stringify(Object.assign(JSON.parse(data),
                {name: pathField.text.trim().split("/").pop()}))
        var err = backend.validateGame(data)
        if (err !== "") {
            errLabel.text = err
            return
        }
        err = backend.saveGame(root.gid, data)
        if (err !== "") {
            errLabel.text = err
            return
        }
        root.close()
    }

    FileDialog {
        id: exePicker
        title: "Select Windows game executable"
        nameFilters: ["Windows executables (*.exe *.EXE)", "All files (*)"]
        onAccepted: {
            var p = backend.fileUrlToPath(exePicker.selectedFile)
            if (p !== "")
                root.pathChosen(p)
        }
    }

    FolderDialog {
        id: dirPicker
        title: "Select RPGMaker game folder"
        onAccepted: {
            var p = backend.fileUrlToPath(dirPicker.selectedFolder)
            if (p !== "")
                root.pathChosen(p)
        }
    }

    // Result of the native (kdialog/zenity) picker started by backend.pickPath.
    Connections {
        target: backend
        function onPathPicked(kind, path) {
            if (path !== "")
                root.pathChosen(path)
        }
    }

    // A Dialog's `padding` does not reach the footer, so wrap the button row
    // in a padded Control to keep the buttons off the wizard's edges.
    footer: Control {
        leftPadding: 16
        rightPadding: 16
        topPadding: 8
        bottomPadding: 16
        implicitWidth: contentItem.implicitWidth + leftPadding + rightPadding
        implicitHeight: contentItem.implicitHeight + topPadding + bottomPadding
        contentItem: RowLayout {
            spacing: 8
            Btn { text: "Cancel"; onClicked: root.close() }
            Item { Layout.fillWidth: true }
            Btn {
                text: "Back"
                enabled: pages.currentIndex > 0
                onClicked: pages.currentIndex--
            }
            Btn {
                text: pages.currentIndex === pages.count - 1 ? "Finish" : "Next"
                onClicked: {
                    if (pages.currentIndex === pages.count - 1)
                        finish()
                    else
                        pages.currentIndex++
                }
            }
        }
    }

    ColumnLayout {
        spacing: 8
        width: availableWidth
        height: availableHeight

        // Underline below the dialog title (matches the top bar divider).
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 1
            color: root.palette.windowText
            opacity: 0.25
        }

        StackLayout {
            id: pages
            objectName: "wizardPages"
            Layout.fillWidth: true
            Layout.fillHeight: true
            clip: true
            currentIndex: 0

            Item {
                ScrollView {
                    id: sv1
                    anchors.fill: parent
                    clip: true
                    contentWidth: availableWidth
                    ColumnLayout {
                        width: sv1.availableWidth
                        spacing: 8
                        Label { text: "Runner" + (root.gid !== "" ? " (locked: runner changes mean re-adding)" : ""); font.bold: true }
                        Repeater {
                            id: runnerRepeater
                            model: root.runnerDescs
                            // CheckBox (square) + exclusive group, so the runner
                            // picker matches the other checkboxes in the wizard.
                            CheckBox {
                                text: modelData
                                checked: index === 0
                                enabled: root.gid === ""
                                ButtonGroup.group: runnerGroup
                                onCheckedChanged: if (checked) root.runner = root.runnerKeys[index]
                            }
                        }
                    }
                }
            }

            Item {
                ScrollView {
                    id: sv2
                    anchors.fill: parent
                    clip: true
                    contentWidth: availableWidth
                    ColumnLayout {
                        width: sv2.availableWidth
                        spacing: 8
                        Label { text: "Game location"; font.bold: true }
                        RowLayout {
                            Layout.fillWidth: true
                            TextField {
                                id: pathField
                                Layout.fillWidth: true
                                placeholderText: "/path/to/game"
                            }
                            Btn {
                                text: "Browse…"
                                onClicked: {
                                    // Native desktop picker (KDE kdialog / zenity);
                                    // QML dialogs only as a last resort.
                                    var target = root.runner === "rpgmaker" ? "dir" : "file"
                                    if (backend.pickPath(target, root.runner))
                                        return
                                    if (target === "dir") {
                                        dirPicker.currentFolder = backend.pathToFileUrl(backend.lastDir())
                                        dirPicker.open()
                                    } else {
                                        var linux = root.runner === "native"
                                        exePicker.title = linux ? "Select game executable"
                                                                : "Select Windows game executable"
                                        exePicker.nameFilters = linux
                                            ? ["All files (*)"]
                                            : ["Windows executables (*.exe *.EXE)", "All files (*)"]
                                        exePicker.selectedNameFilter = exePicker.nameFilters[0]
                                        exePicker.currentFolder = backend.pathToFileUrl(backend.lastDir())
                                        exePicker.open()
                                    }
                                }
                            }
                            Btn {
                                text: "Detect"
                                onClicked: {
                                    var res = backend.detect(pathField.text)
                                    if (res === "") {
                                        root.detectSeverity = "warn"
                                        detectLabel.text = "Detection failed to run."
                                        return
                                    }
                                    var parts = res.split("|")
                                    var r = parts[1], conf = parts[2], detail = parts[4]
                                    var preface = ""
                                    if ((conf === "high" || conf === "medium") && root.runnerKeys.indexOf(r) >= 0) {
                                        setRunner(r)
                                        preface = "Detected: " + detail + " → runner '" + r + "' (" + conf + ")."
                                    } else {
                                        preface = "Detected: " + detail + " (confidence: " + conf + ") — pick the runner manually."
                                    }
                                    reconcileSelection(preface)
                                }
                            }
                        }
                        RowLayout {
                            Layout.fillWidth: true
                            spacing: 8
                            Label {
                                id: detectLabel
                                text: "Tip: Detect fills in the runner from the previous page."
                                opacity: root.detectSeverity === "warn" ? 1.0 : 0.7
                                color: root.detectSeverity === "warn" ? "#e5a50a" : palette.text
                                wrapMode: Text.Wrap
                                Layout.fillWidth: true
                            }
                            Btn {
                                text: "Switch to Proton"
                                visible: root.detectSeverity === "warn" && root.runner === "native"
                                onClicked: {
                                    setRunner("proton")
                                    root.detectSeverity = "ok"
                                    detectLabel.text = "Runner set to proton."
                                }
                            }
                        }
                    }
                }
            }

            Item {
                ScrollView {
                    id: sv3
                    anchors.fill: parent
                    clip: true
                    contentWidth: availableWidth
                    GridLayout {
                        width: sv3.availableWidth
                        columns: 2
                        columnSpacing: 12
                        rowSpacing: 10
                        Label { text: "Filter and performance"; font.bold: true; Layout.columnSpan: 2 }
                        Label { text: "Variant:" }
                        ComboBox {
                            id: variantCombo
                            Layout.fillWidth: true
                        }
                        Label { text: "Game GPU:" }
                        ComboBox {
                            id: gpuCombo
                            Layout.fillWidth: true
                            // Tag the display GPU for display only; the stored
                            // value stays the raw model text (currentText).
                            displayText: root.gpuLabel(currentText)
                            delegate: ItemDelegate {
                                width: gpuCombo.width
                                text: root.gpuLabel(modelData)
                                highlighted: gpuCombo.highlightedIndex === index
                            }
                        }
                        Label {
                            id: gpuWarn
                            visible: root.gpuMismatch()
                            Layout.columnSpan: 2
                            Layout.fillWidth: true
                            wrapMode: Text.WordWrap
                            color: "#e0a030"
                            text: "RPGMaker titles are Chromium/ANGLE-Vulkan and can only filter on the display GPU. "
                                  + "On any other GPU Chromium drops Vulkan — no filter, no overlay, and no WebGL. "
                                  + "Pick the GPU marked (display)."
                        }
                        Label { text: "FPS cap (0 = off):" }
                        SpinBox {
                            id: fpsSpin
                            from: 0
                            to: 480
                            value: 60
                            Layout.fillWidth: true
                        }
                        CheckBox {
                            id: hudCheck
                            text: "Show fps overlay while playing"
                            Layout.columnSpan: 2
                        }
                        Label { text: "Language:" }
                        ComboBox {
                            id: langCombo
                            objectName: "langCombo"
                            Layout.fillWidth: true
                            model: root.localeModel
                        }
                        CheckBox {
                            id: prefixCheck
                            text: "Separate Wine prefix for this game (Proton only)"
                            Layout.columnSpan: 2
                        }
                    }
                }
            }

            Item {
                ScrollView {
                    id: sv4
                    anchors.fill: parent
                    clip: true
                    contentWidth: availableWidth
                    ColumnLayout {
                        width: sv4.availableWidth
                        spacing: 8
                        Label { text: "Name"; font.bold: true }
                        TextField {
                            id: nameField
                            Layout.fillWidth: true
                            placeholderText: "Display name"
                        }
                    }
                }
            }

            Item {
                ScrollView {
                    id: sv5
                    anchors.fill: parent
                    clip: true
                    contentWidth: availableWidth
                    ColumnLayout {
                        width: sv5.availableWidth
                        spacing: 8
                        Label { text: "Translation (Japanese VNs)"; font.bold: true }
                        CheckBox {
                            id: trEnable
                            objectName: "trEnable"
                            text: "Translate Japanese dialogue via DeepL"
                        }
                        Label {
                            text: "Debug"
                            font.bold: true
                            topPadding: 8
                            opacity: trEnable.checked ? 1.0 : 0.5
                        }
                        CheckBox {
                            id: browserCheck
                            objectName: "browserCheck"
                            text: "Show the DeepL browser window (debug)"
                            enabled: trEnable.checked
                        }
                        CheckBox {
                            id: hookerCheck
                            objectName: "hookerCheck"
                            text: "Show Textractor during Setup (debug)"
                            enabled: trEnable.checked
                        }
                        Label {
                            text: "Proton/Windows games only. Filter and translation compose in one launch. First run: enable, then Translate (or Setup Text Hooker) — Textractor stays hidden and the in-app picker records the story thread. Debug boxes reveal the DeepL browser and Textractor."
                            opacity: 0.7
                            wrapMode: Text.Wrap
                            Layout.fillWidth: true
                        }
                    }
                }
            }
        }

        Label {
            id: errLabel
            visible: text !== ""
            wrapMode: Text.Wrap
            Layout.fillWidth: true
        }
    }
}
