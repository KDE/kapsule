/*
    SPDX-FileCopyrightText: 2024-2026 KDE Community
    SPDX-License-Identifier: GPL-3.0-or-later
*/

#include "output.h"
#include "rang.hpp"

#include <Kapsule/KapsuleClient>
#include <Kapsule/Container>
#include <Kapsule/Types>

#include <QCommandLineParser>
#include <QCoreApplication>
#include <QDir>
#include <QFileInfo>
#include <QJsonArray>
#include <QJsonDocument>
#include <QJsonObject>

#include <qcoro/qcorotask.h>
#include <qcoro/qcorocore.h>

#include <unistd.h>
#include <sys/wait.h>
#include <cerrno>
#include <csignal>
#include <cstring>
#include <iomanip>
#include <iostream>

using namespace Kapsule;

// Program name (kap or kapsule) - set at startup
static QString programName;

static bool shouldEmitOsc777()
{
    return isatty(STDOUT_FILENO) == 1;
}

static QString sanitizeOsc777Field(const QString &value)
{
    QString sanitized = value;
    sanitized.remove(QLatin1Char(';'));
    sanitized.remove(QLatin1Char('\a'));
    sanitized.remove(QLatin1Char('\u001b'));
    return sanitized;
}

static void emitOsc777ContainerPush(const QString &containerName)
{
    if (!shouldEmitOsc777()) {
        return;
    }

    const QByteArray safeName = sanitizeOsc777Field(containerName).toUtf8();
    std::cout << "\033]777;container;push;" << safeName.constData() << ";kapsule\a" << std::flush;
}

static void emitOsc777ContainerPop()
{
    if (!shouldEmitOsc777()) {
        return;
    }

    std::cout << "\033]777;container;pop;;\a" << std::flush;
}

// Forward declarations for command handlers
QCoro::Task<int> cmdCreate(KapsuleClient &client, const QStringList &args);
QCoro::Task<int> cmdEnter(KapsuleClient &client, const QStringList &args);
QCoro::Task<int> cmdList(KapsuleClient &client, const QStringList &args);
QCoro::Task<int> cmdStart(KapsuleClient &client, const QStringList &args);
QCoro::Task<int> cmdStop(KapsuleClient &client, const QStringList &args);
QCoro::Task<int> cmdRm(KapsuleClient &client, const QStringList &args);
QCoro::Task<int> cmdConfig(KapsuleClient &client, const QStringList &args);
QCoro::Task<int> cmdImage(KapsuleClient &client, const QStringList &args);
QCoro::Task<int> cmdImageImport(KapsuleClient &client, const QStringList &args);
QCoro::Task<int> cmdImageList(KapsuleClient &client, const QStringList &args);
QCoro::Task<int> cmdImageDelete(KapsuleClient &client, const QStringList &args);

void printUsage()
{
    auto &o = out();
    o.info(QStringLiteral("Usage: %1 <command> [options]").arg(programName).toStdString());
    o.info("");
    o.section("Commands:");
    {
        IndentGuard g(o);
        o.info("create <name>    Create a new container");
        o.info("enter [name]     Enter a container (default if configured)");;
        o.info("list             List containers");
        o.info("start <name>     Start a stopped container");
        o.info("stop <name>      Stop a running container");
        o.info("rm <name>        Remove a container");
        o.info("config           Show configuration");
        o.info("image import     Import a local image");
        o.info("image list       List imported images");
        o.info("image delete     Delete an image");
        o.info("image refresh    Refresh cached images");
    }
    o.info("");
    o.dim(QStringLiteral("Run '%1 <command> --help' for command-specific help.").arg(programName).toStdString());
}

QCoro::Task<int> asyncMain(const QStringList &args)
{
    auto &o = out();

    if (args.size() < 2) {
        printUsage();
        co_return 0;
    }

    QString command = args.at(1);

    // Handle --help and --version at top level
    if (command == QStringLiteral("--help") || command == QStringLiteral("-h")) {
        printUsage();
        co_return 0;
    }

    if (command == QStringLiteral("--version") || command == QStringLiteral("-V")) {
        o.info(QStringLiteral("%1 version %2").arg(programName, QCoreApplication::applicationVersion()).toStdString());
        co_return 0;
    }

    // Create client and check connection
    KapsuleClient client;

    if (!client.isConnected()) {
        o.error("Cannot connect to kapsule-daemon");
        o.hint("Is the daemon running? Try: systemctl status kapsule-daemon");
        co_return 1;
    }

    // Remaining args after command
    QStringList cmdArgs = args.mid(2);

    // Dispatch to command handlers
    if (command == QStringLiteral("create")) {
        co_return co_await cmdCreate(client, cmdArgs);
    } else if (command == QStringLiteral("enter")) {
        co_return co_await cmdEnter(client, cmdArgs);
    } else if (command == QStringLiteral("list") || command == QStringLiteral("ls")) {
        co_return co_await cmdList(client, cmdArgs);
    } else if (command == QStringLiteral("start")) {
        co_return co_await cmdStart(client, cmdArgs);
    } else if (command == QStringLiteral("stop")) {
        co_return co_await cmdStop(client, cmdArgs);
    } else if (command == QStringLiteral("rm") || command == QStringLiteral("remove")) {
        co_return co_await cmdRm(client, cmdArgs);
    } else if (command == QStringLiteral("config")) {
        co_return co_await cmdConfig(client, cmdArgs);
    } else if (command == QStringLiteral("image")) {
        co_return co_await cmdImage(client, cmdArgs);
    } else {
        o.error(QStringLiteral("Unknown command: %1").arg(command).toStdString());
        printUsage();
        co_return 1;
    }
}

// =============================================================================
// Command: create
// =============================================================================

/**
 * Build QCommandLineOption entries and a variant-map builder from the schema.
 *
 * For each schema option the mapping is:
 *   boolean → --<flag> and --no-<flag>  (both registered, default marked)
 *   string  → --<flag> <value>
 *   array   → --<flag> <value>           (repeatable)
 *
 * Returns: list of options to add to the parser.
 */
static QList<QCommandLineOption> schemaToCliOptions(const CreateSchema &schema)
{
    QList<QCommandLineOption> cliOptions;
    for (const auto &opt : schema.allOptions()) {
        const QString flag = opt.cliFlag();

        if (opt.type == QStringLiteral("boolean")) {
            // Register both --<flag> and --no-<flag> so the user can
            // explicitly enable or disable any boolean option.
            if (opt.defaultsToTrue()) {
                cliOptions.append({
                    flag,
                    opt.description + QStringLiteral(" [default]")
                });
                cliOptions.append({
                    QStringLiteral("no-") + flag,
                    QStringLiteral("Disable: ") + opt.title
                });
            } else {
                cliOptions.append({
                    flag,
                    opt.description
                });
                cliOptions.append({
                    QStringLiteral("no-") + flag,
                    QStringLiteral("Disable: ") + opt.title + QStringLiteral(" [default]")
                });
            }
        } else if (opt.type == QStringLiteral("string")) {
            cliOptions.append({
                flag,
                opt.description,
                opt.title  // value name shown in help
            });
        } else if (opt.type == QStringLiteral("array")) {
            cliOptions.append({
                flag,
                opt.description + QStringLiteral(" (repeatable)"),
                opt.title
            });
        }
    }
    return cliOptions;
}

/**
 * After parsing, walk the schema and build a QVariantMap containing only
 * the options the user explicitly set (non-default values).  The daemon
 * fills in defaults for anything omitted.
 */
static QVariantMap cliToVariantMap(const QCommandLineParser &parser,
                                   const CreateSchema &schema)
{
    QVariantMap map;
    for (const auto &opt : schema.allOptions()) {
        const QString flag = opt.cliFlag();

        if (opt.type == QStringLiteral("boolean")) {
            const bool hasPositive = parser.isSet(flag);
            const bool hasNegative = parser.isSet(QStringLiteral("no-") + flag);

            // Last-one-wins if the user passes both (unlikely but harmless).
            // Only insert when explicitly set so the daemon uses its defaults
            // for anything omitted.
            if (hasPositive && !hasNegative) {
                map.insert(opt.key, true);
            } else if (hasNegative && !hasPositive) {
                map.insert(opt.key, false);
            }
        } else if (opt.type == QStringLiteral("string")) {
            if (parser.isSet(flag)) {
                map.insert(opt.key, parser.value(flag));
            }
        } else if (opt.type == QStringLiteral("array")) {
            QStringList values = parser.values(flag);
            if (!values.isEmpty()) {
                map.insert(opt.key, values);
            }
        }
    }
    return map;
}

QCoro::Task<int> cmdCreate(KapsuleClient &client, const QStringList &args)
{
    auto &o = out();

    // ---- Fetch the option schema from the daemon ----
    QString schemaJson = co_await client.getCreateSchema();
    if (schemaJson.isEmpty()) {
        o.error("Failed to retrieve create-container schema from daemon");
        co_return 1;
    }
    CreateSchema schema = parseCreateSchema(schemaJson);
    if (schema.version == 0) {
        o.error("Failed to parse create-container schema");
        co_return 1;
    }

    // ---- Build parser dynamically ----
    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral("Create a new kapsule container"));
    parser.addHelpOption();
    parser.addPositionalArgument(QStringLiteral("name"), QStringLiteral("Name of the container to create"));

    // The --image flag is not part of the schema (it is a separate D-Bus parameter)
    parser.addOption({{QStringLiteral("i"), QStringLiteral("image")},
                      QStringLiteral("Base image to use (e.g., images:ubuntu/24.04)"),
                      QStringLiteral("image")});

    // Add schema-driven flags
    const auto schemaOptions = schemaToCliOptions(schema);
    for (const auto &cliOpt : schemaOptions) {
        parser.addOption(cliOpt);
    }

    // ---- Parse ----
    QStringList fullArgs = QStringList{programName + QStringLiteral(" create")} + args;
    if (!parser.parse(fullArgs)) {
        o.error(parser.errorText().toStdString());
        co_return 1;
    }

    if (parser.isSet(QStringLiteral("help"))) {
        std::cout << parser.helpText().toStdString();
        co_return 0;
    }

    QStringList positional = parser.positionalArguments();
    if (positional.isEmpty()) {
        o.error("Container name required");
        o.hint(QStringLiteral("Usage: %1 create <name> [--image <image>]").arg(programName).toStdString());
        co_return 1;
    }

    QString name = positional.at(0);
    QString image = parser.value(QStringLiteral("image"));

    // Build variant map from user-specified flags only
    QVariantMap optionsMap = cliToVariantMap(parser, schema);

    o.section(QStringLiteral("Creating container: %1").arg(name).toStdString());

    auto result = co_await client.createContainer(name, image, optionsMap,
        [&o](MessageType type, const QString &msg, int indent) {
            o.print(type, msg.toStdString(), indent);
        });

    if (!result.success) {
        o.failure(result.error.toStdString());
        co_return 1;
    }

    o.success("Container created");
    co_return 0;
}

// =============================================================================
// Command: enter
// =============================================================================

QCoro::Task<int> cmdEnter(KapsuleClient &client, const QStringList &args)
{
    auto &o = out();

    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral("Enter a kapsule container"));
    parser.addHelpOption();
    parser.addPositionalArgument(QStringLiteral("name"), QStringLiteral("Container name (optional, uses default)"));
    parser.addPositionalArgument(QStringLiteral("command"), QStringLiteral("Command to run (optional)"));

    QStringList fullArgs = QStringList{programName + QStringLiteral(" enter")} + args;
    if (!parser.parse(fullArgs)) {
        o.error(parser.errorText().toStdString());
        co_return 1;
    }

    if (parser.isSet(QStringLiteral("help"))) {
        std::cout << parser.helpText().toStdString();
        co_return 0;
    }

    QStringList positional = parser.positionalArguments();

    // Handle "--" separator for commands
    QString containerName;
    QStringList command;

    int dashIdx = args.indexOf(QStringLiteral("--"));
    if (dashIdx >= 0) {
        // Everything before -- could be container name
        if (dashIdx > 0) {
            containerName = args.at(0);
        }
        // Everything after -- is the command
        command = args.mid(dashIdx + 1);
    } else if (!positional.isEmpty()) {
        containerName = positional.at(0);
        command = positional.mid(1);
    }

    auto config = co_await client.config();
    if (config.contains(QStringLiteral("error"))) {
        o.error(config.value(QStringLiteral("error")).toString().toStdString());
        co_return 1;
    }

    const QString defaultContainer = config.value(QStringLiteral("default_container")).toString();
    const QString defaultImage = config.value(QStringLiteral("default_image")).toString();
    const QString targetContainer = containerName.isEmpty() ? defaultContainer : containerName;

    if (!targetContainer.isEmpty() && targetContainer == defaultContainer) {
        bool containerExists = false;
        const auto containers = co_await client.listContainers();
        for (const auto &container : containers) {
            if (container.name() == targetContainer) {
                containerExists = true;
                break;
            }
        }

        if (!containerExists) {
            o.section(QStringLiteral("Creating container: %1").arg(targetContainer).toStdString());
            auto createResult = co_await client.createContainer(targetContainer, defaultImage, {},
                [&o](MessageType type, const QString &msg, int indent) {
                    o.print(type, msg.toStdString(), indent);
                });

            if (!createResult.success
                && !createResult.error.contains(QStringLiteral("already exists"), Qt::CaseInsensitive)) {
                o.failure(createResult.error.toStdString());
                co_return 1;
            }
        }
    }

    auto result = co_await client.prepareEnter(containerName, command);

    if (!result.success) {
        o.error(result.error.toStdString());
        co_return 1;
    }

    // Execute the command (replaces current process)
    QByteArrayList execArgsBytes;
    for (const QString &arg : result.execArgs) {
        execArgsBytes.append(arg.toLocal8Bit());
    }

    std::vector<char *> execArgv;
    for (QByteArray &arg : execArgsBytes) {
        execArgv.push_back(arg.data());
    }
    execArgv.push_back(nullptr);

    if (!shouldEmitOsc777()) {
        execvp(execArgv[0], execArgv.data());

        // If we get here, exec failed
        o.error(QStringLiteral("Failed to exec: %1").arg(QString::fromLocal8Bit(strerror(errno))).toStdString());
        co_return 1;
    }

    pid_t childPid = fork();
    if (childPid < 0) {
        o.error(QStringLiteral("Failed to fork: %1").arg(QString::fromLocal8Bit(strerror(errno))).toStdString());
        co_return 1;
    }

    if (childPid == 0) {
        emitOsc777ContainerPush(targetContainer);
        execvp(execArgv[0], execArgv.data());
        std::cerr << "Failed to exec: " << strerror(errno) << '\n';
        _exit(127);
    }

    int status = 0;
    pid_t waited = -1;
    do {
        waited = waitpid(childPid, &status, 0);
    } while (waited == -1 && errno == EINTR);

    emitOsc777ContainerPop();

    if (waited == -1) {
        o.error(QStringLiteral("Failed to wait for child: %1").arg(QString::fromLocal8Bit(strerror(errno))).toStdString());
        co_return 1;
    }

    if (WIFEXITED(status)) {
        co_return WEXITSTATUS(status);
    }

    if (WIFSIGNALED(status)) {
        const int sig = WTERMSIG(status);
        std::signal(sig, SIG_DFL);
        std::raise(sig);
        co_return 128 + sig;
    }

    co_return 1;
}

// =============================================================================
// Command: list
// =============================================================================

QCoro::Task<int> cmdList(KapsuleClient &client, const QStringList &args)
{
    auto &o = out();

    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral("List kapsule containers"));
    parser.addHelpOption();
    parser.addOptions({
        {{QStringLiteral("r"), QStringLiteral("running")},
         QStringLiteral("Show only running containers")},
        {{QStringLiteral("a"), QStringLiteral("all")},
         QStringLiteral("Show all containers including stopped (default)")},
    });

    QStringList fullArgs = QStringList{programName + QStringLiteral(" list")} + args;
    if (!parser.parse(fullArgs)) {
        o.error(parser.errorText().toStdString());
        co_return 1;
    }

    if (parser.isSet(QStringLiteral("help"))) {
        std::cout << parser.helpText().toStdString();
        co_return 0;
    }

    const bool showRunningOnly = parser.isSet(QStringLiteral("running"));

    auto containers = co_await client.listContainers();

    if (containers.isEmpty()) {
        o.dim("No containers found.");
        co_return 0;
    }

    // Filter if --running
    if (showRunningOnly) {
        containers.erase(
            std::remove_if(containers.begin(), containers.end(),
                [](const Container &c) { return c.state() != Container::State::Running; }),
            containers.end());

        if (containers.isEmpty()) {
            o.dim("No running containers.");
            co_return 0;
        }
    }

    // Print table header
    std::cout << rang::style::bold
              << std::left << std::setw(20) << "NAME"
              << std::setw(12) << "STATUS"
              << std::setw(25) << "IMAGE"
              << std::setw(12) << "MODE"
              << "CREATED"
              << rang::style::reset << '\n';

    // Print rows
    for (const Container &c : containers) {
        std::string status;
        switch (c.state()) {
        case Container::State::Running:
            std::cout << rang::fg::green;
            status = "Running";
            break;
        case Container::State::Stopped:
            std::cout << rang::fg::red;
            status = "Stopped";
            break;
        case Container::State::Starting:
            std::cout << rang::fg::yellow;
            status = "Starting";
            break;
        case Container::State::Stopping:
            std::cout << rang::fg::yellow;
            status = "Stopping";
            break;
        default:
            std::cout << rang::fg::gray;
            status = "Unknown";
        }

        std::cout << std::left << std::setw(20) << c.name().toStdString()
                  << std::setw(12) << status
                  << rang::fg::reset
                  << std::setw(25) << c.image().toStdString()
                  << std::setw(12) << containerModeToString(c.mode()).toStdString()
                  << c.created().toString(Qt::ISODate).left(10).toStdString()
                  << '\n';
    }

    co_return 0;
}

// =============================================================================
// Command: start
// =============================================================================

QCoro::Task<int> cmdStart(KapsuleClient &client, const QStringList &args)
{
    auto &o = out();

    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral("Start a stopped container"));
    parser.addHelpOption();
    parser.addPositionalArgument(QStringLiteral("name"), QStringLiteral("Container name"));

    QStringList fullArgs = QStringList{programName + QStringLiteral(" start")} + args;
    if (!parser.parse(fullArgs)) {
        o.error(parser.errorText().toStdString());
        co_return 1;
    }

    if (parser.isSet(QStringLiteral("help"))) {
        std::cout << parser.helpText().toStdString();
        co_return 0;
    }

    QStringList positional = parser.positionalArguments();
    if (positional.isEmpty()) {
        o.error("Container name required");
        co_return 1;
    }

    QString name = positional.at(0);

    o.section(QStringLiteral("Starting container: %1").arg(name).toStdString());

    auto result = co_await client.startContainer(name,
        [&o](MessageType type, const QString &msg, int indent) {
            o.print(type, msg.toStdString(), indent);
        });

    if (!result.success) {
        o.failure(result.error.toStdString());
        co_return 1;
    }

    o.success("Container started");
    co_return 0;
}

// =============================================================================
// Command: stop
// =============================================================================

QCoro::Task<int> cmdStop(KapsuleClient &client, const QStringList &args)
{
    auto &o = out();

    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral("Stop a running container"));
    parser.addHelpOption();
    parser.addPositionalArgument(QStringLiteral("name"), QStringLiteral("Container name"));
    parser.addOptions({
        {{QStringLiteral("f"), QStringLiteral("force")},
         QStringLiteral("Force stop the container")},
    });

    QStringList fullArgs = QStringList{programName + QStringLiteral(" stop")} + args;
    if (!parser.parse(fullArgs)) {
        o.error(parser.errorText().toStdString());
        co_return 1;
    }

    if (parser.isSet(QStringLiteral("help"))) {
        std::cout << parser.helpText().toStdString();
        co_return 0;
    }

    QStringList positional = parser.positionalArguments();
    if (positional.isEmpty()) {
        o.error("Container name required");
        co_return 1;
    }

    QString name = positional.at(0);
    bool force = parser.isSet(QStringLiteral("force"));

    o.section(QStringLiteral("Stopping container: %1").arg(name).toStdString());

    auto result = co_await client.stopContainer(name, force,
        [&o](MessageType type, const QString &msg, int indent) {
            o.print(type, msg.toStdString(), indent);
        });

    if (!result.success) {
        o.failure(result.error.toStdString());
        co_return 1;
    }

    o.success("Container stopped");
    co_return 0;
}

// =============================================================================
// Command: rm
// =============================================================================

QCoro::Task<int> cmdRm(KapsuleClient &client, const QStringList &args)
{
    auto &o = out();

    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral("Remove a container"));
    parser.addHelpOption();
    parser.addPositionalArgument(QStringLiteral("name"), QStringLiteral("Container name"));
    parser.addOptions({
        {{QStringLiteral("f"), QStringLiteral("force")},
         QStringLiteral("Force removal even if running")},
    });

    QStringList fullArgs = QStringList{programName + QStringLiteral(" rm")} + args;
    if (!parser.parse(fullArgs)) {
        o.error(parser.errorText().toStdString());
        co_return 1;
    }

    if (parser.isSet(QStringLiteral("help"))) {
        std::cout << parser.helpText().toStdString();
        co_return 0;
    }

    QStringList positional = parser.positionalArguments();
    if (positional.isEmpty()) {
        o.error("Container name required");
        co_return 1;
    }

    QString name = positional.at(0);
    bool force = parser.isSet(QStringLiteral("force"));

    o.section(QStringLiteral("Removing container: %1").arg(name).toStdString());

    auto result = co_await client.deleteContainer(name, force,
        [&o](MessageType type, const QString &msg, int indent) {
            o.print(type, msg.toStdString(), indent);
        });

    if (!result.success) {
        o.failure(result.error.toStdString());
        co_return 1;
    }

    o.success("Container removed");
    co_return 0;
}

// =============================================================================
// Command: config
// =============================================================================

QCoro::Task<int> cmdConfig(KapsuleClient &client, const QStringList &args)
{
    auto &o = out();

    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral("View kapsule configuration"));
    parser.addHelpOption();
    parser.addPositionalArgument(QStringLiteral("key"), QStringLiteral("Config key to display (optional)"));

    QStringList fullArgs = QStringList{programName + QStringLiteral(" config")} + args;
    if (!parser.parse(fullArgs)) {
        o.error(parser.errorText().toStdString());
        co_return 1;
    }

    if (parser.isSet(QStringLiteral("help"))) {
        std::cout << parser.helpText().toStdString();
        co_return 0;
    }

    QStringList positional = parser.positionalArguments();
    QString key = positional.value(0);

    auto config = co_await client.config();

    if (config.contains(QStringLiteral("error"))) {
        o.error(config.value(QStringLiteral("error")).toString().toStdString());
        co_return 1;
    }

    if (key.isEmpty()) {
        // Show all config
        o.section("Configuration");
        {
            IndentGuard g(o);
            o.info(QStringLiteral("default_container: %1")
                .arg(config.value(QStringLiteral("default_container")).toString())
                .toStdString());
            o.info(QStringLiteral("default_image: %1")
                .arg(config.value(QStringLiteral("default_image")).toString())
                .toStdString());
        }
    } else {
        // Show single key
        QStringList validKeys = {QStringLiteral("default_container"), QStringLiteral("default_image")};
        if (!validKeys.contains(key)) {
            o.error(QStringLiteral("Unknown config key: %1").arg(key).toStdString());
            o.hint(QStringLiteral("Valid keys: %1").arg(validKeys.join(QStringLiteral(", "))).toStdString());
            co_return 1;
        }
        o.info(QStringLiteral("%1 = %2").arg(key, config.value(key).toString()).toStdString());
    }

    co_return 0;
}

// =============================================================================
// Command: image
// =============================================================================

QCoro::Task<int> cmdImageRefresh(KapsuleClient &client, const QStringList &args);

QCoro::Task<int> cmdImage(KapsuleClient &client, const QStringList &args)
{
    auto &o = out();

    if (args.isEmpty()) {
        o.info(QStringLiteral("Usage: %1 image <subcommand>").arg(programName).toStdString());
        o.info("");
        o.section("Subcommands:");
        {
            IndentGuard g(o);
            o.info("import <path>            Import a local image");
            o.info("list                     List imported images");
            o.info("delete <id>              Delete an image");
            o.info("refresh [server:alias]   Refresh cached images");
        }
        co_return 0;
    }

    QString subcommand = args.at(0);
    QStringList subArgs = args.mid(1);

    if (subcommand == QStringLiteral("import")) {
        co_return co_await cmdImageImport(client, subArgs);
    } else if (subcommand == QStringLiteral("list") || subcommand == QStringLiteral("ls")) {
        co_return co_await cmdImageList(client, subArgs);
    } else if (subcommand == QStringLiteral("delete") || subcommand == QStringLiteral("rm")) {
        co_return co_await cmdImageDelete(client, subArgs);
    } else if (subcommand == QStringLiteral("refresh")) {
        co_return co_await cmdImageRefresh(client, subArgs);
    } else {
        o.error(QStringLiteral("Unknown image subcommand: %1").arg(subcommand).toStdString());
        co_return 1;
    }
}

QCoro::Task<int> cmdImageRefresh(KapsuleClient &client, const QStringList &args)
{
    auto &o = out();

    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral("Refresh cached images from upstream"));
    parser.addHelpOption();
    parser.addPositionalArgument(
        QStringLiteral("image"),
        QStringLiteral("Image to refresh in server:alias format (e.g., kapsule:archlinux). "
                       "Omit to refresh all auto-update images."));

    QStringList fullArgs = QStringList{programName + QStringLiteral(" image refresh")} + args;
    if (!parser.parse(fullArgs)) {
        o.error(parser.errorText().toStdString());
        co_return 1;
    }

    if (parser.isSet(QStringLiteral("help"))) {
        std::cout << parser.helpText().toStdString();
        co_return 0;
    }

    QStringList positional = parser.positionalArguments();
    QString imageSpec = positional.value(0);

    if (imageSpec.isEmpty()) {
        o.section("Refreshing all cached images");
    } else {
        o.section(QStringLiteral("Refreshing image: %1").arg(imageSpec).toStdString());
    }

    auto result = co_await client.refreshImages(imageSpec,
        [&o](MessageType type, const QString &msg, int indent) {
            o.print(type, msg.toStdString(), indent);
        });

    if (!result.success) {
        o.failure(result.error.toStdString());
        co_return 1;
    }

    co_return 0;
}

// =============================================================================
// Command: image import
// =============================================================================

QCoro::Task<int> cmdImageImport(KapsuleClient &client, const QStringList &args)
{
    auto &o = out();

    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral("Import a local image into kapsule"));
    parser.addHelpOption();
    parser.addPositionalArgument(QStringLiteral("path"),
        QStringLiteral("Directory containing the built image (e.g., out/archlinux/)"));
    parser.addOption({{QStringLiteral("a"), QStringLiteral("alias")},
                      QStringLiteral("Alias name for the image (defaults to directory name)"),
                      QStringLiteral("name")});

    QStringList fullArgs = QStringList{programName + QStringLiteral(" image import")} + args;
    if (!parser.parse(fullArgs)) {
        o.error(parser.errorText().toStdString());
        co_return 1;
    }

    if (parser.isSet(QStringLiteral("help"))) {
        std::cout << parser.helpText().toStdString();
        co_return 0;
    }

    QStringList positional = parser.positionalArguments();
    if (positional.isEmpty()) {
        o.error("Image path required");
        o.hint(QStringLiteral("Usage: %1 image import <path> [--alias <name>]").arg(programName).toStdString());
        co_return 1;
    }

    QString path = positional.at(0);
    QString alias = parser.value(QStringLiteral("alias"));

    // Derive alias from directory name if not specified
    if (alias.isEmpty()) {
        alias = QDir(path).dirName();
    }

    o.section(QStringLiteral("Importing image: %1").arg(alias).toStdString());

    auto result = co_await client.importImage(path, alias,
        [&o](MessageType type, const QString &msg, int indent) {
            o.print(type, msg.toStdString(), indent);
        });

    if (!result.success) {
        o.failure(result.error.toStdString());
        co_return 1;
    }

    o.success(QStringLiteral("Image imported successfully as \"%1\"").arg(alias).toStdString());
    co_return 0;
}

// =============================================================================
// Command: image list
// =============================================================================

static QString formatImageSize(qint64 bytes)
{
    if (bytes < 0) {
        return QStringLiteral("-");
    }
    constexpr qint64 GB = 1024LL * 1024 * 1024;
    constexpr qint64 MB = 1024LL * 1024;
    if (bytes >= GB) {
        return QStringLiteral("%1 GB").arg(static_cast<double>(bytes) / GB, 0, 'f', 1);
    }
    return QStringLiteral("%1 MB").arg(static_cast<double>(bytes) / MB, 0, 'f', 1);
}

QCoro::Task<int> cmdImageList(KapsuleClient &client, const QStringList &args)
{
    auto &o = out();

    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral("List imported images"));
    parser.addHelpOption();

    QStringList fullArgs = QStringList{programName + QStringLiteral(" image list")} + args;
    if (!parser.parse(fullArgs)) {
        o.error(parser.errorText().toStdString());
        co_return 1;
    }

    if (parser.isSet(QStringLiteral("help"))) {
        std::cout << parser.helpText().toStdString();
        co_return 0;
    }

    QString json = co_await client.listImages();
    if (json.isEmpty()) {
        o.error("Failed to retrieve image list from daemon");
        co_return 1;
    }

    QJsonParseError parseError;
    QJsonDocument doc = QJsonDocument::fromJson(json.toUtf8(), &parseError);
    if (parseError.error != QJsonParseError::NoError) {
        o.error(QStringLiteral("Failed to parse image list: %1").arg(parseError.errorString()).toStdString());
        co_return 1;
    }

    QJsonArray images = doc.array();
    if (images.isEmpty()) {
        o.dim("No images found.");
        co_return 0;
    }

    // Print table header
    std::cout << rang::style::bold
              << std::left << std::setw(14) << "FINGERPRINT"
              << std::setw(20) << "ALIAS"
              << std::setw(30) << "DESCRIPTION"
              << std::setw(10) << "SIZE"
              << "UPLOADED"
              << rang::style::reset << '\n';

    // Print rows
    for (const QJsonValue &val : images) {
        QJsonObject img = val.toObject();

        QString fingerprint = img.value(QStringLiteral("fingerprint")).toString().left(12);
        QString description = img.value(QStringLiteral("description")).toString();
        qint64 size = img.value(QStringLiteral("size")).toInteger(-1);
        QString uploaded = img.value(QStringLiteral("uploaded_at")).toString().left(10);

        // Extract first alias
        QString alias;
        QJsonArray aliases = img.value(QStringLiteral("aliases")).toArray();
        if (!aliases.isEmpty()) {
            alias = aliases.first().toObject().value(QStringLiteral("name")).toString();
        }

        std::cout << std::left << std::setw(14) << fingerprint.toStdString()
                  << std::setw(20) << alias.toStdString()
                  << std::setw(30) << description.left(28).toStdString()
                  << std::setw(10) << formatImageSize(size).toStdString()
                  << uploaded.toStdString()
                  << '\n';
    }

    co_return 0;
}

// =============================================================================
// Command: image delete
// =============================================================================

QCoro::Task<int> cmdImageDelete(KapsuleClient &client, const QStringList &args)
{
    auto &o = out();

    QCommandLineParser parser;
    parser.setApplicationDescription(QStringLiteral("Delete an image by alias or fingerprint"));
    parser.addHelpOption();
    parser.addPositionalArgument(QStringLiteral("identifier"),
        QStringLiteral("Image alias or fingerprint"));

    QStringList fullArgs = QStringList{programName + QStringLiteral(" image delete")} + args;
    if (!parser.parse(fullArgs)) {
        o.error(parser.errorText().toStdString());
        co_return 1;
    }

    if (parser.isSet(QStringLiteral("help"))) {
        std::cout << parser.helpText().toStdString();
        co_return 0;
    }

    QStringList positional = parser.positionalArguments();
    if (positional.isEmpty()) {
        o.error("Image identifier required");
        o.hint(QStringLiteral("Usage: %1 image delete <fingerprint-or-alias>").arg(programName).toStdString());
        co_return 1;
    }

    QString identifier = positional.at(0);

    o.section(QStringLiteral("Deleting image: %1").arg(identifier).toStdString());

    auto result = co_await client.deleteImage(identifier,
        [&o](MessageType type, const QString &msg, int indent) {
            o.print(type, msg.toStdString(), indent);
        });

    if (!result.success) {
        o.failure(result.error.toStdString());
        co_return 1;
    }

    o.success("Image deleted");
    co_return 0;
}

// =============================================================================
// Main entry point
// =============================================================================

int main(int argc, char *argv[])
{
    QCoreApplication app(argc, argv);
    
    // Detect program name from argv[0] (kap or kapsule)
    QString argv0 = QString::fromLocal8Bit(argv[0]);
    programName = QFileInfo(argv0).fileName();
    // Normalize to either "kap" or "kapsule"
    if (programName != QStringLiteral("kap")) {
        programName = QStringLiteral("kapsule");
    }
    
    app.setApplicationName(programName);
    app.setApplicationVersion(QStringLiteral("0.2.1"));  // TODO: Get from build
    app.setOrganizationDomain(QStringLiteral("kde.org"));
    app.setOrganizationName(QStringLiteral("KDE"));

    return QCoro::waitFor(asyncMain(app.arguments()));
}
