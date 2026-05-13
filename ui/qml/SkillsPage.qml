import QtQuick
import QtQuick.Controls
import QtQuick.Layouts
import QtQuick.Controls.Material
import "components"

Item {
    Component.onCompleted: backend.refreshSkillEntries()

    ScrollView {
        anchors.fill: parent
        anchors.margins: 10
        contentWidth: availableWidth
        clip: true

        ColumnLayout {
            width: parent.width
            spacing: Theme.spacingMedium

            Card {
                title: "Config Context"
                Layout.fillWidth: true

                RowLayout {
                    width: parent.width
                    spacing: Theme.spacingSmall

                    ActionButton {
                        text: "Client 1"
                        baseColor: backend.activeConfigClient === 0 ? Theme.accent : Theme.bgTertiary
                        hoverColor: backend.activeConfigClient === 0 ? Theme.accentHover : Theme.borderLight
                        Layout.fillWidth: true
                        implicitHeight: 32
                        onClicked: backend.activeConfigClient = 0
                    }

                    ActionButton {
                        text: "Client 2"
                        baseColor: backend.activeConfigClient === 1 ? Theme.accent : Theme.bgTertiary
                        hoverColor: backend.activeConfigClient === 1 ? Theme.accentHover : Theme.borderLight
                        Layout.fillWidth: true
                        implicitHeight: 32
                        onClicked: backend.activeConfigClient = 1
                    }
                }
            }

            Card {
                title: "Skill Profile"
                Layout.fillWidth: true

                RowLayout {
                    width: parent.width
                    spacing: Theme.spacingSmall

                    Label {
                        text: "Character Profile"
                        color: Theme.textPrimary
                        font { pixelSize: 10; family: "Segoe UI" }
                        Layout.fillWidth: true
                    }

                    ComboBox {
                        id: profileCombo
                        Layout.preferredWidth: 180
                        model: backend.availableSkillProfiles
                        Material.background: Theme.bgPrimary
                        Material.foreground: Theme.textPrimary
                        font { pixelSize: 10; family: "Segoe UI" }

                        function findProfileIndex(profileName) {
                            for (let i = 0; i < profileCombo.count; i++) {
                                if (profileCombo.textAt(i) === profileName) {
                                    return i
                                }
                            }
                            return -1
                        }

                        onActivated: {
                            if (currentText !== backend.activeSkillProfile) {
                                backend.activeSkillProfile = currentText
                            }
                        }

                        Component.onCompleted: {
                            const idx = findProfileIndex(backend.activeSkillProfile)
                            if (idx >= 0) {
                                currentIndex = idx
                            }
                        }

                        Connections {
                            target: backend
                            function onActiveSkillProfileChanged() {
                                const idx = profileCombo.findProfileIndex(backend.activeSkillProfile)
                                if (idx >= 0) {
                                    profileCombo.currentIndex = idx
                                }
                            }

                            function onActiveConfigClientChanged() {
                                const idx = profileCombo.findProfileIndex(backend.activeSkillProfile)
                                if (idx >= 0) {
                                    profileCombo.currentIndex = idx
                                }
                            }
                        }
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
                    font { pixelSize: 10; family: "Segoe UI" }
                    wrapMode: Text.WordWrap
                }
            }

            Timer {
                interval: 2000
                running: true
                repeat: true
                onTriggered: backend.refreshSkillEntries()
            }

            Item { height: 10 }
        }
    }
}
