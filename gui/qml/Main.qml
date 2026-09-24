import QtQuick
import QtQuick.Controls
import QtQuick.Layouts

ApplicationWindow {
    id: root
    objectName: "mainWindow"
    title: "Migaki"
    width: 1100
    height: 700
    visible: true
    property string gid: ""

    SystemPalette { id: sys; colorGroup: SystemPalette.Active }

    function refreshDetails() {
        detailText.text = root.gid === "" ? "Select a game." : backend.gameDetails(root.gid)
        detailIcon.source = root.gid === "" ? "" : backend.iconFor(root.gid)
        logView.clear()
        if (root.gid !== "")
            logView.append("— launch to populate logs —\n")
    }

    function selectIndex(i) {
        root.gid = (i >= 0 && i < gameList.count) ? gamesModel.gidAt(i) : ""
        refreshDetails()
    }

    function ensureSelection() {
        if (gameList.count === 0) {
            selectIndex(-1)
        } else if (gameList.currentIndex < 0) {
            gameList.currentIndex = 0
        } else {
            selectIndex(gameList.currentIndex)
        }
    }

    function err(text) {
        promptDlg.ask("Notice", text, ["OK"], "local")
    }

    Component.onCompleted: ensureSelection()

    header: ColumnLayout {
        spacing: 0
        RowLayout {
            Layout.fillWidth: true
            MenuBarItem {
                text: "Settings"
                onClicked: { settingsDlg.load(); settingsDlg.open() }
            }
            MenuBarItem {
                text: "Quit"
                onClicked: root.close()
            }
            Item { Layout.fillWidth: true }
        }
        Rectangle {
            Layout.fillWidth: true
            Layout.preferredHeight: 1
            color: sys.windowText
            opacity: 0.25
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: 10
        spacing: 10

        ColumnLayout {
            Layout.preferredWidth: 300
            Layout.fillHeight: true
            spacing: 6
            Label { text: "Games"; font.bold: true }
            ListView {
                id: gameList
                Layout.fillWidth: true
                Layout.fillHeight: true
                clip: true
                model: gamesModel
                onCurrentIndexChanged: root.selectIndex(currentIndex)
                onCountChanged: root.ensureSelection()
                delegate: ItemDelegate {
                    width: gameList.width
                    onClicked: gameList.currentIndex = index
                    contentItem: RowLayout {
                        spacing: 8
                        Image {
                            source: model.icon
                            Layout.preferredWidth: 32
                            Layout.preferredHeight: 32
                            fillMode: Image.PreserveAspectFit
                            visible: model.icon !== ""
                        }
                        ColumnLayout {
                            spacing: 0
                            Layout.fillWidth: true
                            Label { text: model.name; elide: Text.ElideRight; Layout.fillWidth: true }
                            Label { text: model.info; opacity: 0.7; font.pointSize: 8; elide: Text.ElideRight; Layout.fillWidth: true }
                        }
                    }
                }
            }
            RowLayout {
                Btn { text: "Add…"; Layout.fillWidth: true; onClicked: wizard.start("") }
                Btn {
                    text: "Edit…"
                    Layout.fillWidth: true
                    enabled: root.gid !== ""
                    onClicked: wizard.start(root.gid)
                }
                Btn {
                    text: "Remove"
                    Layout.fillWidth: true
                    enabled: root.gid !== ""
                    onClicked: promptDlg.ask("Remove game", "Remove this game from the library?", ["Remove", "Cancel"], "delete")
                }
            }
        }

        Rectangle {
            Layout.fillHeight: true
            Layout.preferredWidth: 1
            color: sys.windowText
            opacity: 0.25
        }

        ColumnLayout {
            Layout.fillWidth: true
            Layout.fillHeight: true
            spacing: 8

            RowLayout {
                Image {
                    id: detailIcon
                    Layout.preferredWidth: 64
                    Layout.preferredHeight: 64
                    fillMode: Image.PreserveAspectFit
                    visible: status === Image.Ready
                }
                Rectangle {
                    Layout.preferredWidth: 1
                    Layout.preferredHeight: detailIcon.height
                    Layout.alignment: Qt.AlignVCenter
                    color: sys.windowText
                    opacity: 0.25
                    visible: detailIcon.visible
                }
                Label {
                    id: detailText
                    text: "Select a game."
                    wrapMode: Text.Wrap
                    textFormat: Text.RichText
                    Layout.fillWidth: true
                }
            }

            Flow {
                Layout.fillWidth: true
                spacing: 6
                Btn {
                    text: "Launch"
                    enabled: root.gid !== "" && !backend.running
                    onClicked: {
                        var r = backend.launchGame(root.gid, false)
                        if (r !== "" && r !== "pending")
                            err(r)
                    }
                }
                Btn {
                    text: "Launch unfiltered (A/B)"
                    enabled: root.gid !== "" && !backend.running
                    onClicked: {
                        var r = backend.launchGame(root.gid, true)
                        if (r !== "" && r !== "pending")
                            err(r)
                    }
                }
                Btn {
                    text: "Translate"
                    enabled: root.gid !== "" && !backend.running
                    onClicked: {
                        var r = backend.translateGame(root.gid, false)
                        if (r !== "" && r !== "pending")
                            err(r)
                    }
                }
                Btn {
                    text: "Setup Text Hooker for translation"
                    enabled: root.gid !== "" && !backend.running
                    onClicked: {
                        var r = backend.translateGame(root.gid, true)
                        if (r !== "" && r !== "pending")
                            err(r)
                    }
                }
                Btn {
                    text: "Textbox"
                    onClicked: backend.openTextbox(root.gid)
                }
                Btn {
                    text: "Stop"
                    enabled: backend.running
                    onClicked: backend.stopGame()
                }
                Btn {
                    text: "Preview command"
                    enabled: root.gid !== ""
                    onClicked: {
                        previewLabel.text = backend.previewCommand(root.gid)
                        previewDlg.open()
                    }
                }
            }

            Label { text: backend.statusText }
            Label { text: backend.bridgeText; opacity: 0.7; font.pointSize: 8 }

            TextArea {
                id: logView
                objectName: "logView"
                Layout.fillWidth: true
                Layout.fillHeight: true
                readOnly: true
                wrapMode: TextEdit.Wrap
                placeholderText: "Launch output appears here…"
                font.family: "monospace"
            }
        }
    }

    Wizard { id: wizard; objectName: "wizardDlg" }
    SettingsDlg { id: settingsDlg; objectName: "settingsDlg" }
    PickThreadDlg { id: pickThreadDlg; objectName: "pickThreadDlg" }
    PromptDlg { id: promptDlg; objectName: "promptDlg" }

    Dialog {
        id: previewDlg
        title: "Resolved command"
        modal: true
        width: Math.min(640, previewDlg.parent ? previewDlg.parent.width - 48 : 640)
        padding: 16
        anchors.centerIn: parent
        standardButtons: Dialog.Ok
        ColumnLayout {
            spacing: 8
            width: previewDlg.availableWidth
            // Underline below the dialog title (matches the top bar divider).
            Rectangle {
                Layout.fillWidth: true
                Layout.preferredHeight: 1
                color: previewDlg.palette.windowText
                opacity: 0.25
            }
            Label {
                id: previewLabel
                Layout.fillWidth: true
                wrapMode: Text.Wrap
            }
        }
    }

    Connections {
        target: backend
        function onLogAppended(line) { logView.append(line); logView.cursorPosition = logView.length }
        function onLogCleared() { logView.clear() }
        function onPrompt(title, text, buttons) { promptDlg.ask(title, text, JSON.parse(buttons)) }
        function onGamesChanged() { root.ensureSelection() }
        function onSetupLaunched(gid) { pickThreadDlg.start(gid, true) }
    }

    Connections {
        target: promptDlg
        function onDone(result) {
            if (promptDlg.tag === "delete" && result === 0 && root.gid !== "")
                backend.removeGame(root.gid)
        }
    }

    Connections {
        target: gamesModel
        function onIconResolved(gid, path) {
            if (gid === root.gid && path !== "")
                detailIcon.source = "file://" + path
        }
    }
}
