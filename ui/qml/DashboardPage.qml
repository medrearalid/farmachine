import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Controls.Material
import "components"

Item {
    id: dashPage
    property int slot1Index: 0
    property int slot2Index: 0

    ScrollView {
        anchors.fill: parent
        anchors.margins: 10
        contentWidth: availableWidth
        clip: true

        ColumnLayout {
            width: parent.width
            spacing: Theme.spacingMedium

            // ─── Hero Card ───
            Card {
                title: ""
                Layout.fillWidth: true

                Label {
                    text: "AI Vision Automation"
                    font { pixelSize: 14; bold: true; family: "Segoe UI" }
                    color: Theme.accent
                    Layout.alignment: Qt.AlignHCenter
                    anchors.horizontalCenter: parent.horizontalCenter
                }
                Label {
                    text: "YOLO Object Detection Engine"
                    font { pixelSize: 9; family: "Segoe UI" }
                    color: Theme.textMuted
                    anchors.horizontalCenter: parent.horizontalCenter
                }
            }

            // ─── Process Selector ───
            Card {
                title: "Window Selection"
                Layout.fillWidth: true

                RowLayout {
                    width: parent.width
                    spacing: Theme.spacingSmall

                    Label {
                        text: "Slot 1"
                        color: Theme.textMuted
                        font { pixelSize: 10; family: "Segoe UI" }
                        Layout.preferredWidth: 42
                    }
                    ComboBox {
                        id: slot1Combo
                        Layout.fillWidth: true
                        Layout.minimumWidth: 180
                        model: backend.windowList
                        Material.background: Theme.bgPrimary
                        Material.foreground: Theme.textPrimary
                        displayText: count > 0 ? currentText : "Select a window..."
                        onCurrentIndexChanged: dashPage.slot1Index = currentIndex
                    }
                    ActionButton {
                        text: backend.slot1Attached ? "✓" : "Attach"
                        baseColor: backend.slot1Attached ? Theme.success : Theme.accent
                        hoverColor: backend.slot1Attached ? Theme.successHover : Theme.accentHover
                        Layout.preferredWidth: 74
                        implicitHeight: 32
                        enabled: slot1Combo.count > 0
                        onClicked: backend.attachWindowToSlot(1, slot1Combo.currentIndex)
                    }
                }

                RowLayout {
                    width: parent.width
                    spacing: Theme.spacingSmall

                    Label {
                        text: "Slot 2"
                        color: Theme.textMuted
                        font { pixelSize: 10; family: "Segoe UI" }
                        Layout.preferredWidth: 42
                    }
                    ComboBox {
                        id: slot2Combo
                        Layout.fillWidth: true
                        Layout.minimumWidth: 180
                        model: backend.windowList
                        Material.background: Theme.bgPrimary
                        Material.foreground: Theme.textPrimary
                        displayText: count > 0 ? currentText : "Select a window..."
                        onCurrentIndexChanged: dashPage.slot2Index = currentIndex
                    }
                    ActionButton {
                        text: backend.slot2Attached ? "✓" : "Attach"
                        baseColor: backend.slot2Attached ? Theme.success : Theme.accent
                        hoverColor: backend.slot2Attached ? Theme.successHover : Theme.accentHover
                        Layout.preferredWidth: 74
                        implicitHeight: 32
                        enabled: slot2Combo.count > 0
                        onClicked: backend.attachWindowToSlot(2, slot2Combo.currentIndex)
                    }
                }

                RowLayout {
                    width: parent.width
                    spacing: Theme.spacingSmall

                    Item { Layout.preferredWidth: 42 }

                    StatusBadge {
                        text: "C1: " + backend.slot1WindowName
                        badgeColor: backend.slot1Attached ? Theme.success : Theme.danger
                    }
                    StatusBadge {
                        text: "C2: " + backend.slot2WindowName
                        badgeColor: backend.slot2Attached ? Theme.success : Theme.danger
                    }
                    Item { Layout.fillWidth: true }
                    ActionButton {
                        text: "⟳"
                        baseColor: Theme.bgTertiary
                        hoverColor: Theme.borderLight
                        implicitWidth: 34; implicitHeight: 32
                        onClicked: backend.refreshWindows()
                    }
                }

                ToggleOption {
                    text: "Show all windows (advanced)"
                    checked: false
                    onToggled: backend.setShowAllWindows(checked)
                }
            }

            // ─── Start / Stop ───
            ActionButton {
                text: backend.isRunning ? "◼  STOP" : "▶  START"
                baseColor: backend.isRunning ? Theme.danger : Theme.success
                hoverColor: backend.isRunning ? Theme.dangerHover : Theme.successHover
                Layout.fillWidth: true
                implicitHeight: 42
                font { pixelSize: 13; bold: true }
                enabled: backend.slot1Attached || backend.slot2Attached
                onClicked: backend.toggleBot()
            }

            // ─── Quick Actions ───
            RowLayout {
                Layout.fillWidth: true
                spacing: 8

                ActionButton {
                    text: "Calibrate Mouse"
                    baseColor: Theme.bgTertiary
                    hoverColor: Theme.borderLight
                    Layout.fillWidth: true
                    implicitHeight: 32
                    onClicked: backend.calibrateMouse()
                }
            }

            // ─── Status Bar ───
            Card {
                title: ""
                Layout.fillWidth: true

                RowLayout {
                    width: parent.width
                    Label {
                        text: "Status: " + backend.statusText
                        color: backend.isRunning ? Theme.success : Theme.textMuted
                        font { pixelSize: 11; family: "Segoe UI" }
                    }
                    Item { Layout.fillWidth: true }
                    Label {
                        text: "Targets: " + backend.destroyedCount + " | " + backend.elapsedTime
                        color: Theme.textMuted
                        font { pixelSize: 9; family: "Segoe UI" }
                    }
                }
            }

            // ─── Log Viewer ───
            Card {
                title: "Log"
                Layout.fillWidth: true
                Layout.fillHeight: true
                Layout.minimumHeight: 150

                ScrollView {
                    width: parent.width
                    height: 150
                    clip: true
                    TextArea {
                        id: logArea
                        readOnly: true
                        text: backend.logText
                        color: "#8b949e"
                        font { pixelSize: 10; family: "Consolas" }
                        wrapMode: TextArea.Wrap
                        background: Rectangle { color: Theme.bgPrimary; radius: Theme.radiusSmall }

                        onTextChanged: {
                            cursorPosition = length
                        }
                    }
                }
            }
            Item { height: 10 }
        }
    }
}
