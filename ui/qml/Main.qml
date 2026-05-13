import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Controls.Material
import "components"

ApplicationWindow {
    id: root
    visible: false
    width: 1160
    height: 780
    minimumWidth: 980
    minimumHeight: 660
    title: "FARMACHINE"
    property string hostThemeMode: Theme.darkMode ? "dark" : "light"

    property int currentTab: 0
    property bool logExpanded: false

    color: "transparent"
    Material.theme: Theme.darkMode ? Material.Dark : Material.Light
    Material.accent: Theme.accent

    function applyTabContext(index) {
        if (index === 1) {
            backend.activeConfigClient = 0
            backend.refreshSkillEntries()
        } else if (index === 2) {
            backend.activeConfigClient = 1
            backend.refreshSkillEntries()
        }
    }

    onCurrentTabChanged: applyTabContext(currentTab)

    Component.onCompleted: applyTabContext(currentTab)

    background: Rectangle {
        anchors.fill: parent
        gradient: Gradient {
            GradientStop { position: 0.0; color: Theme.bgPrimary }
            GradientStop { position: 1.0; color: Theme.bgSecondary }
        }

        Rectangle {
            width: 260
            height: 260
            radius: 130
            x: -90
            y: -120
            color: Theme.darkMode ? "#334AA3FF" : "#1F2D9CDB"
        }

        Rectangle {
            width: 220
            height: 220
            radius: 110
            anchors.right: parent.right
            anchors.bottom: parent.bottom
            anchors.margins: -70
            color: Theme.darkMode ? "#2A2ECC71" : "#162ECC71"
        }
    }

    RowLayout {
        anchors.fill: parent
        anchors.margins: Theme.spacingLarge
        spacing: Theme.spacingLarge

        Rectangle {
            id: controlPanel
            Layout.preferredWidth: 258
            Layout.maximumWidth: 258
            Layout.fillHeight: true
            radius: Theme.radiusLarge
            color: Theme.panelBackground
            border.width: 1
            border.color: Theme.border

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: Theme.spacingLarge
                spacing: Theme.spacingMedium

                Label {
                    text: "Control Panel"
                    color: Theme.textPrimary
                    font { pixelSize: 26; family: "Segoe UI Semibold" }
                }

                Rectangle {
                    Layout.fillWidth: true
                    implicitHeight: 1
                    color: Theme.border
                }

                Rectangle {
                    Layout.fillWidth: true
                    radius: Theme.radiusMedium
                    color: Theme.cardBackground
                    border.width: 1
                    border.color: Theme.border
                    implicitHeight: 130

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: Theme.spacingMedium
                        spacing: 4

                        Label {
                            text: backend.isRunning ? "Running" : "Idle"
                            color: backend.isRunning ? Theme.success : Theme.warning
                            font { pixelSize: 26; family: "Consolas" }
                            horizontalAlignment: Text.AlignHCenter
                            Layout.alignment: Qt.AlignHCenter
                        }

                        Label {
                            text: backend.elapsedTime
                            color: Theme.textPrimary
                            font { pixelSize: 22; family: "Consolas" }
                            horizontalAlignment: Text.AlignHCenter
                            Layout.alignment: Qt.AlignHCenter
                        }

                        Label {
                            text: backend.statusText
                            color: Theme.textMuted
                            font.pixelSize: 10
                            horizontalAlignment: Text.AlignHCenter
                            wrapMode: Text.WordWrap
                            Layout.fillWidth: true
                        }
                    }
                }

                ActionButton {
                    text: "START"
                    baseColor: "#2ECC71"
                    hoverColor: "#28B463"
                    textColor: "#FFFFFF"
                    Layout.fillWidth: true
                    implicitHeight: 46
                    enabled: !backend.isRunning && (backend.slot1Attached || backend.slot2Attached)
                    onClicked: {
                        if (!backend.isRunning)
                            backend.toggleBot()
                    }
                }

                ActionButton {
                    text: "STOP (Del)"
                    baseColor: Theme.darkMode ? "#5B607B" : "#A9B0BD"
                    hoverColor: Theme.darkMode ? "#505570" : "#8C96A5"
                    textColor: "#F2F5FA"
                    Layout.fillWidth: true
                    implicitHeight: 42
                    enabled: backend.isRunning
                    onClicked: {
                        if (backend.isRunning)
                            backend.toggleBot()
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spacingSmall

                    Label {
                        text: "TopMost"
                        color: Theme.textPrimary
                        font.pixelSize: 11
                        Layout.fillWidth: true
                    }

                    ToggleSwitch {
                        checked: backend.alwaysOnTop
                        onToggled: function(state) {
                            backend.alwaysOnTop = state
                        }
                    }
                }

                RowLayout {
                    Layout.fillWidth: true
                    spacing: Theme.spacingSmall

                    Label {
                        text: "Theme"
                        color: Theme.textPrimary
                        font.pixelSize: 11
                        Layout.fillWidth: true
                    }

                    Label {
                        text: Theme.darkMode ? "Dark" : "Light"
                        color: Theme.textMuted
                        font.pixelSize: 10
                    }

                    ToggleSwitch {
                        checked: Theme.darkMode
                        activeColor: Theme.accent
                        onToggled: function(state) {
                            Theme.darkMode = state
                        }
                    }
                }

                ActionButton {
                    text: "Refresh Windows"
                    baseColor: Theme.bgTertiary
                    hoverColor: Theme.borderLight
                    Layout.fillWidth: true
                    implicitHeight: 34
                    onClicked: backend.refreshWindows()
                }

                Item { Layout.fillHeight: true }

                Rectangle {
                    Layout.fillWidth: true
                    radius: Theme.radiusMedium
                    color: Theme.cardBackground
                    border.width: 1
                    border.color: Theme.border
                    implicitHeight: root.logExpanded ? 190 : 54

                    Behavior on implicitHeight {
                        NumberAnimation { duration: 140; easing.type: Easing.OutCubic }
                    }

                    ColumnLayout {
                        anchors.fill: parent
                        anchors.margins: Theme.spacingSmall
                        spacing: Theme.spacingSmall

                        RowLayout {
                            Layout.fillWidth: true

                            Label {
                                text: "Log"
                                color: Theme.textPrimary
                                font.pixelSize: 11
                                Layout.fillWidth: true
                            }

                            ActionButton {
                                text: root.logExpanded ? "Hide" : "Show"
                                baseColor: Theme.bgTertiary
                                hoverColor: Theme.borderLight
                                implicitHeight: 28
                                Layout.preferredWidth: 66
                                onClicked: root.logExpanded = !root.logExpanded
                            }
                        }

                        ScrollView {
                            Layout.fillWidth: true
                            Layout.fillHeight: true
                            visible: root.logExpanded
                            clip: true

                            TextArea {
                                readOnly: true
                                text: backend.logText
                                color: Theme.textMuted
                                font { pixelSize: 10; family: "Consolas" }
                                wrapMode: TextArea.Wrap

                                background: Rectangle {
                                    color: Theme.inputBackground
                                    radius: Theme.radiusSmall
                                    border.width: 1
                                    border.color: Theme.border
                                }

                                onTextChanged: cursorPosition = length
                            }
                        }
                    }
                }
            }
        }

        Rectangle {
            id: contentPanel
            Layout.fillWidth: true
            Layout.fillHeight: true
            radius: Theme.radiusLarge
            color: Theme.panelBackground
            border.width: 1
            border.color: Theme.border

            ColumnLayout {
                anchors.fill: parent
                anchors.margins: Theme.spacingLarge
                spacing: Theme.spacingMedium

                RowLayout {
                    Layout.fillWidth: true

                    Label {
                        text: "FARM"
                        color: Theme.textPrimary
                        font { pixelSize: 32; family: "Segoe UI Semibold" }
                    }

                    Item { Layout.fillWidth: true }

                    Label {
                        text: backend.slot1Attached || backend.slot2Attached
                            ? "Attached Slots: " + (backend.slot1Attached ? "1" : "") + (backend.slot2Attached ? " 2" : "")
                            : "Attached Slots: none"
                        color: Theme.textPrimary
                        font { pixelSize: 12; family: "Consolas" }
                    }

                    Rectangle {
                        width: 44
                        height: 24
                        radius: 4
                        color: backend.isRunning ? Theme.success : Theme.bgTertiary

                        Label {
                            anchors.centerIn: parent
                            text: backend.isRunning ? "RUN" : "IDLE"
                            color: Theme.darkMode ? "#0E111A" : "#FFFFFF"
                            font { pixelSize: 10; family: "Segoe UI Semibold" }
                        }
                    }
                }

                TabBar {
                    id: topTabs
                    Layout.fillWidth: true
                    currentIndex: root.currentTab
                    spacing: Theme.spacingSmall
                    background: Rectangle {
                        color: "transparent"
                    }

                    TabButton {
                        text: "Global Settings"
                        font.pixelSize: 12
                        contentItem: Text {
                            text: parent.text
                            color: topTabs.currentIndex === 0 ? Theme.textPrimary : Theme.textMuted
                            font.pixelSize: 12
                            font.bold: topTabs.currentIndex === 0
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                        background: Rectangle {
                            radius: Theme.radiusSmall
                            color: topTabs.currentIndex === 0 ? Theme.cardBackground : "transparent"
                            border.width: 1
                            border.color: topTabs.currentIndex === 0 ? Theme.borderLight : Theme.border
                        }
                    }

                    TabButton {
                        text: "Client 1"
                        font.pixelSize: 12
                        contentItem: Text {
                            text: parent.text
                            color: topTabs.currentIndex === 1 ? Theme.textPrimary : Theme.textMuted
                            font.pixelSize: 12
                            font.bold: topTabs.currentIndex === 1
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                        background: Rectangle {
                            radius: Theme.radiusSmall
                            color: topTabs.currentIndex === 1 ? Theme.cardBackground : "transparent"
                            border.width: 1
                            border.color: topTabs.currentIndex === 1 ? Theme.borderLight : Theme.border
                        }
                    }

                    TabButton {
                        text: "Client 2"
                        font.pixelSize: 12
                        contentItem: Text {
                            text: parent.text
                            color: topTabs.currentIndex === 2 ? Theme.textPrimary : Theme.textMuted
                            font.pixelSize: 12
                            font.bold: topTabs.currentIndex === 2
                            horizontalAlignment: Text.AlignHCenter
                            verticalAlignment: Text.AlignVCenter
                        }
                        background: Rectangle {
                            radius: Theme.radiusSmall
                            color: topTabs.currentIndex === 2 ? Theme.cardBackground : "transparent"
                            border.width: 1
                            border.color: topTabs.currentIndex === 2 ? Theme.borderLight : Theme.border
                        }
                    }

                    onCurrentIndexChanged: root.currentTab = currentIndex
                }

                StackLayout {
                    Layout.fillWidth: true
                    Layout.fillHeight: true
                    currentIndex: root.currentTab

                    GlobalSettingsTab {
                        pageActive: root.currentTab === 0
                    }

                    ClientSettingsTab {
                        clientIndex: 0
                        pageActive: root.currentTab === 1
                    }

                    ClientSettingsTab {
                        clientIndex: 1
                        pageActive: root.currentTab === 2
                    }
                }
            }
        }
    }
}
