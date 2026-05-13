import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import "components"

Item {
    id: clientTab
    required property int clientIndex
    property bool pageActive: false
    property string clientName: clientIndex === 0 ? "Client 1" : "Client 2"

    function activateContext() {
        if (backend.activeConfigClient !== clientIndex)
            backend.activeConfigClient = clientIndex
    }

    function syncProfileSelection() {
        if (profileCombo.count === 0)
            return
        const idx = profileCombo.findProfileIndex(backend.activeSkillProfile)
        if (idx >= 0)
            profileCombo.currentIndex = idx
    }

    onPageActiveChanged: {
        if (!pageActive)
            return
        activateContext()
        backend.refreshSkillEntries()
        syncProfileSelection()
    }

    ScrollView {
        anchors.fill: parent
        anchors.margins: Theme.spacingLarge
        contentWidth: availableWidth
        clip: true

        ColumnLayout {
            width: parent.width
            spacing: Theme.spacingLarge

            Card {
                title: clientName + " - Vision & Combat"
                Layout.fillWidth: true

                SliderOption {
                    text: "Detection Confidence"
                    from: 0.1
                    to: 1.0
                    stepSize: 0.05
                    value: backend.yoloConfidence
                    onValueEdited: backend.yoloConfidence = value
                }

                ComboOption {
                    text: "Target Selection"
                    modelData: ["Nearest", "Random", "Largest"]
                    currentIndex: backend.selectionModeIndex
                    onSelectionChanged: backend.selectionModeIndex = currentIndex
                }

                SpinOption {
                    text: "Combat Timeout"
                    from: 10
                    to: 300
                    value: backend.combatTimeout
                    suffix: "s"
                    onValueChanged: backend.combatTimeout = value
                }

                SpinOption {
                    text: "Scan Radius"
                    from: 100
                    to: 2000
                    value: backend.scanRadius
                    suffix: "px"
                    onValueChanged: backend.scanRadius = value
                }

                ToggleOption {
                    text: "Enable Multi-Target Queue"
                    checked: backend.multiTargetQueueEnabled
                    onToggled: backend.multiTargetQueueEnabled = checked
                }

                SpinOption {
                    text: "Queue Size"
                    from: 1
                    to: 999
                    value: backend.multiTargetQueueSize
                    enabled: backend.multiTargetQueueEnabled
                    opacity: enabled ? 1.0 : 0.55
                    onValueChanged: backend.multiTargetQueueSize = value
                }

                SpinOption {
                    text: "Deferred Queue Delay"
                    from: 0
                    to: 10
                    value: backend.deferredQueueClickDelay
                    suffix: "s"
                    enabled: backend.multiTargetQueueEnabled
                    opacity: enabled ? 1.0 : 0.55
                    onValueChanged: backend.deferredQueueClickDelay = value
                }

                RowLayout {
                    width: parent.width
                    spacing: Theme.spacingSmall

                    ActionButton {
                        text: "Mask UI Regions (" + backend.maskRegionCount + ")"
                        baseColor: Theme.bgTertiary
                        hoverColor: Theme.borderLight
                        implicitHeight: 36
                        Layout.preferredWidth: 170
                        onClicked: backend.startMaskRegionSelection()
                    }

                    Item { Layout.fillWidth: true }
                }
            }

            Card {
                title: clientName + " - Timings"
                Layout.fillWidth: true

                SpinOption {
                    text: "Revive Delay"
                    from: 1
                    to: 30
                    value: backend.reviveDelay
                    suffix: "s"
                    onValueChanged: backend.reviveDelay = value
                }

                SpinOption {
                    text: "Skill Check Interval"
                    from: 1
                    to: 120
                    value: backend.skillCheckInterval
                    suffix: "s"
                    onValueChanged: backend.skillCheckInterval = value
                }

                SpinOption {
                    text: "Quest Check Interval"
                    from: 1
                    to: 60
                    value: backend.questCheckInterval
                    suffix: "s"
                    onValueChanged: backend.questCheckInterval = value
                }

                SpinOption {
                    text: "Miss Timeout"
                    from: 1
                    to: 10
                    value: backend.missTimeout
                    suffix: "s"
                    onValueChanged: backend.missTimeout = value
                }

                SpinOption {
                    text: "Movement Timeout"
                    from: 3
                    to: 30
                    value: backend.movementTimeout
                    suffix: "s"
                    onValueChanged: backend.movementTimeout = value
                }

                SpinOption {
                    text: "Verify Timeout"
                    from: 1
                    to: 10
                    value: backend.verifyTimeout
                    suffix: "s"
                    onValueChanged: backend.verifyTimeout = value
                }

                SpinOption {
                    text: "Strafe Start Delay"
                    from: 1
                    to: 10
                    value: backend.strafeStartDelay
                    suffix: "s"
                    onValueChanged: backend.strafeStartDelay = value
                }

                SpinOption {
                    text: "Strafe Interval"
                    from: 1
                    to: 5
                    value: backend.strafeInterval
                    suffix: "s"
                    onValueChanged: backend.strafeInterval = value
                }
            }

            Card {
                title: clientName + " - Automation"
                Layout.fillWidth: true

                ToggleOption { text: "Auto Loot"; checked: backend.autoLoot; onToggled: backend.autoLoot = checked }
                ToggleOption { text: "Auto Skills"; checked: backend.autoSkills; onToggled: backend.autoSkills = checked }
                ToggleOption { text: "Auto Revive"; checked: backend.autoRevive; onToggled: backend.autoRevive = checked }
                ToggleOption { text: "Anti-Stuck System"; checked: backend.antiStuck; onToggled: backend.antiStuck = checked }
                ToggleOption { text: "Captcha Solver"; checked: backend.captchaSolver; onToggled: backend.captchaSolver = checked }
                ToggleOption { text: "Auto Quest Book"; checked: backend.questEnabled; onToggled: backend.questEnabled = checked }
                ToggleOption { text: "Debug Mode"; checked: backend.debugMode; onToggled: backend.debugMode = checked }
            }

            Card {
                title: clientName + " - Skill Profile"
                Layout.fillWidth: true

                RowLayout {
                    width: parent.width
                    spacing: Theme.spacingSmall

                    Label {
                        text: "Character Profile"
                        color: Theme.textPrimary
                        font.pixelSize: 11
                        Layout.fillWidth: true
                    }

                    ComboBox {
                        id: profileCombo
                        Layout.preferredWidth: 220
                        model: backend.availableSkillProfiles
                        font.pixelSize: 11

                        function findProfileIndex(profileName) {
                            for (let i = 0; i < profileCombo.count; i++) {
                                if (profileCombo.textAt(i) === profileName)
                                    return i
                            }
                            return -1
                        }

                        contentItem: Text {
                            text: profileCombo.displayText
                            color: Theme.textPrimary
                            font.pixelSize: 11
                            verticalAlignment: Text.AlignVCenter
                            leftPadding: 10
                            rightPadding: 30
                            elide: Text.ElideRight
                        }

                        background: Rectangle {
                            color: Theme.inputBackground
                            radius: Theme.radiusSmall
                            border.width: 1
                            border.color: profileCombo.hovered ? Theme.borderLight : Theme.border
                        }

                        onActivated: {
                            if (currentText !== backend.activeSkillProfile)
                                backend.activeSkillProfile = currentText
                        }

                        Component.onCompleted: clientTab.syncProfileSelection()
                    }
                }
            }

            Repeater {
                model: backend.skillEntries

                delegate: Card {
                    showTitle: false
                    Layout.fillWidth: true

                    KeyBindOption {
                        text: modelData.displayName
                        iconSource: modelData.iconPath
                        key: modelData.key
                        cooldown: modelData.cooldown
                        skillEnabled: modelData.enabled

                        onKeyEdited: function(value) {
                            backend.setSkillEntryKey(modelData.slotId, value)
                        }

                        onCooldownEdited: function(value) {
                            backend.setSkillEntryCooldown(modelData.slotId, value)
                        }

                        onEnabledEdited: function(value) {
                            backend.setSkillEntryEnabled(modelData.slotId, value)
                        }
                    }
                }
            }

            Card {
                visible: backend.skillEntries.length === 0
                showTitle: false
                Layout.fillWidth: true

                Label {
                    text: "Bu profile ait skill gorseli bulunamadi. assets/skills/<profil>/ klasorune .png/.jpg ekleyin."
                    color: Theme.textMuted
                    font.pixelSize: 10
                    wrapMode: Text.WordWrap
                }
            }

            RowLayout {
                Layout.fillWidth: true
                spacing: Theme.spacingSmall

                ActionButton {
                    text: "Refresh Skills"
                    baseColor: Theme.bgTertiary
                    hoverColor: Theme.borderLight
                    implicitHeight: 36
                    Layout.preferredWidth: 130
                    onClicked: backend.refreshSkillEntries()
                }

                Item { Layout.fillWidth: true }

                ActionButton {
                    text: "Save " + clientName + ""
                    baseColor: Theme.accent
                    hoverColor: Theme.accentHover
                    implicitHeight: 36
                    Layout.preferredWidth: 130
                    onClicked: backend.saveConfig()
                }
            }

            Timer {
                interval: 2500
                running: clientTab.pageActive
                repeat: true
                onTriggered: backend.refreshSkillEntries()
            }

            Item { height: 10 }
        }
    }

    Connections {
        target: backend

        function onActiveConfigClientChanged() {
            if (!clientTab.pageActive)
                return
            if (backend.activeConfigClient !== clientTab.clientIndex)
                return
            clientTab.syncProfileSelection()
        }

        function onActiveSkillProfileChanged() {
            if (!clientTab.pageActive)
                return
            if (backend.activeConfigClient !== clientTab.clientIndex)
                return
            clientTab.syncProfileSelection()
        }
    }
}
