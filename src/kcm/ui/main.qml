/*
    SPDX-FileCopyrightText: 2026 KDE Community
    SPDX-License-Identifier: GPL-3.0-or-later
*/

import QtQuick
import QtQuick.Layouts
import QtQuick.Controls as QQC2

import org.kde.kirigami as Kirigami
import org.kde.kcmutils as KCMUtils

KCMUtils.ScrollViewKCM {
    id: root

    KCMUtils.ConfigModule.buttons: KCMUtils.ConfigModule.NoAdditionalButton

    implicitWidth: Kirigami.Units.gridUnit * 40
    implicitHeight: Kirigami.Units.gridUnit * 25

    headerPaddingEnabled: false

    header: ColumnLayout {
        spacing: 0

        // Error / status banner
        Kirigami.InlineMessage {
            Layout.fillWidth: true
            visible: kcm.statusMessage.length > 0
            text: kcm.statusMessage
            type: Kirigami.MessageType.Error
        }

        // Not-connected banner
        Kirigami.InlineMessage {
            Layout.fillWidth: true
            visible: !kcm.connected
            text: i18n("Cannot connect to kapsule-daemon. Is the service running?")
            type: Kirigami.MessageType.Warning
        }

        // Toolbar
        QQC2.ToolBar {
            Layout.fillWidth: true
            visible: kcm.connected

            RowLayout {
                anchors.fill: parent

                QQC2.Label {
                    text: i18n("Containers")
                    font.bold: true
                    Layout.leftMargin: Kirigami.Units.largeSpacing
                }

                Item { Layout.fillWidth: true }

                QQC2.BusyIndicator {
                    running: kcm.loading
                    visible: kcm.loading
                    implicitHeight: Kirigami.Units.iconSizes.medium
                    implicitWidth: Kirigami.Units.iconSizes.medium
                }

                QQC2.ToolButton {
                    icon.name: "view-refresh"
                    text: i18n("Refresh")
                    display: QQC2.AbstractButton.IconOnly
                    onClicked: kcm.refresh()
                    enabled: !kcm.loading
                    QQC2.ToolTip.text: text
                    QQC2.ToolTip.visible: hovered
                }

                QQC2.ToolButton {
                    icon.name: "list-add"
                    text: i18n("Create Container")
                    onClicked: kcm.push("CreateContainerPage.qml")
                    enabled: !kcm.loading && kcm.connected
                }
            }
        }
    }

    view: ListView {
        id: containerList
        model: kcm.containerModel
        currentIndex: -1

        Kirigami.PlaceholderMessage {
            anchors.centerIn: parent
            width: parent.width - Kirigami.Units.gridUnit * 4
            visible: containerList.count === 0 && !kcm.loading && kcm.connected
            icon.name: "utilities-terminal"
            text: i18n("No Containers")
            explanation: i18n("Create your first container to get started.")

            helpfulAction: Kirigami.Action {
                icon.name: "list-add"
                text: i18n("Create Container")
                onTriggered: kcm.push("CreateContainerPage.qml")
            }
        }

        delegate: QQC2.ItemDelegate {
            id: containerDelegate
            width: containerList.width

            contentItem: RowLayout {
                spacing: Kirigami.Units.largeSpacing

                // Container icon
                Kirigami.Icon {
                    source: "utilities-terminal"
                    implicitWidth: Kirigami.Units.iconSizes.medium
                    implicitHeight: Kirigami.Units.iconSizes.medium
                }

                // Name + details
                ColumnLayout {
                    Layout.fillWidth: true
                    spacing: Kirigami.Units.smallSpacing

                    QQC2.Label {
                        text: model.name
                        font.bold: true
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                    }

                    QQC2.Label {
                        text: model.image
                        font.pointSize: Kirigami.Theme.smallFont.pointSize
                        color: Kirigami.Theme.disabledTextColor
                        Layout.fillWidth: true
                        elide: Text.ElideRight
                    }
                }

                // Mode badge
                QQC2.Label {
                    text: model.mode
                    visible: model.mode !== "Default"
                    font.pointSize: Kirigami.Theme.smallFont.pointSize
                    color: Kirigami.Theme.disabledTextColor
                }

                // State badge
                Rectangle {
                    implicitWidth: stateLabel.implicitWidth + Kirigami.Units.largeSpacing * 2
                    implicitHeight: stateLabel.implicitHeight + Kirigami.Units.smallSpacing * 2
                    radius: height / 2
                    color: {
                        switch (model.containerState) {
                        case 2: return Kirigami.Theme.positiveBackgroundColor;  // Running
                        case 1: return Kirigami.Theme.neutralBackgroundColor;   // Stopped
                        default: return Kirigami.Theme.backgroundColor;
                        }
                    }

                    QQC2.Label {
                        id: stateLabel
                        anchors.centerIn: parent
                        text: model.stateString
                        font.pointSize: Kirigami.Theme.smallFont.pointSize
                    }
                }

                // Action buttons
                QQC2.ToolButton {
                    icon.name: "media-playback-start"
                    visible: model.containerState === 1  // Stopped
                    onClicked: kcm.startContainer(model.name)
                    enabled: !kcm.loading
                    QQC2.ToolTip.text: i18n("Start")
                    QQC2.ToolTip.visible: hovered
                }

                QQC2.ToolButton {
                    icon.name: "media-playback-stop"
                    visible: model.containerState === 2  // Running
                    onClicked: kcm.stopContainer(model.name)
                    enabled: !kcm.loading
                    QQC2.ToolTip.text: i18n("Stop")
                    QQC2.ToolTip.visible: hovered
                }

                QQC2.ToolButton {
                    icon.name: "edit-delete"
                    onClicked: deleteDialog.open()
                    enabled: !kcm.loading
                    QQC2.ToolTip.text: i18n("Delete")
                    QQC2.ToolTip.visible: hovered

                    Kirigami.PromptDialog {
                        id: deleteDialog
                        title: i18n("Delete Container")
                        subtitle: i18n("Are you sure you want to delete \"%1\"? This cannot be undone.", model.name)
                        standardButtons: Kirigami.Dialog.Ok | Kirigami.Dialog.Cancel
                        onAccepted: kcm.deleteContainer(model.name)
                    }
                }
            }
        }
    }
}
