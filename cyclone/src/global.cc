#include <limits>
#include "global.hh"

using std::map;
using std::shared_ptr;
using std::vector;
using std::set;
using std::pair;
using std::runtime_error;
using std::string;
using std::function;
using std::move;

GlobalRouter::GlobalRouter(uint32_t num_iteration)
    : Router(), num_iteration_(num_iteration) { }

void GlobalRouter::route() {
    index_node_history_table();
    // the actual routing part
    // algorithm based on PathFinder with modification for CGRA architecture
    // TODO:
    // make it domain-specific

    // 1. reorder the nets so that nets with register sinks are routed
    //    first to determine the register position at each iteration. this is
    //    fine since the relative ordering of these two groups (with and
    //    and without) doesn't matter
    // 2. calculate the slack ratio to determine what kind od routing algorithm
    //    to use. either RSMT or shortest-path for each pin pairs
    // 3. actually perform iterations (described in PathFiner)

    group_reg_nets();
    reorder_reg_nets();

    ::map<::pair<::shared_ptr<Node>, ::shared_ptr<Node>>, double> slack_ratio;

    for (uint32_t it = 0; it < num_iteration_; it++) {
        fail_count_ = 0;
        // update the slack ratio table
        compute_slack_ratio(slack_ratio, it);
        for (auto &net : netlist_) {
            route_net(net, it);
        }
        if (!overflow()) {
            return;
        }
        // update the history table
        update_node_history_table();
    }
    if (overflow())
        throw ::runtime_error("unable to route. sorry!");
}

void GlobalRouter::compute_slack_ratio(::map<::pair<::shared_ptr<Node>,
                                                    ::shared_ptr<Node>>,
                                             double> &ratio,
                                       uint32_t current_iter) {
    // Note
    // this is slightly different from the PathFinder
    // here we compute slack ratio for each pin pair, rather than for every
    // possible routable node pair. this reduces the computation intensity
    // significantly.
    if (current_iter == 0) {
        // all timing-driven first thus 1 for every routing pair
        // also notice that for pins that haven't got assigned, this is
        // fine since nullptr will be entered into the ratio, which really
        // doesn't matter since everything is 1
        for (auto &net : netlist_) {
            // FIXME
            // refactor the net to distinguish the source and sinks
            const auto &src = net[0].node;
            for (uint32_t i = 1; i < net.size(); i++) {
                auto const &sink = net[i].node;
                ratio[{src, sink}] = 1;
            }
        }
    } else {
        // traverse the segments to find the actual delay
        double max_delay = 0;
        for (auto &net : netlist_) {
            const auto &src = net[0].node;
            const auto &segments = current_routes[net.id];
            if (src == nullptr)
                throw ::runtime_error("unable to find src when compute slack"
                                      "ratio");
            for (uint32_t i = 1; i < net.size(); i++) {
                auto const &sink = net[i].node;
                // find the routes
                if (sink == nullptr)
                    throw ::runtime_error("unable to find sink when compute"
                                          "slack ratio");
                auto const &route = segments.at(sink);
                double delay = 0;
                for (const auto &node : route) {
                    delay += node->delay;
                }
                ratio[{src, sink}] = delay;
                if (delay > max_delay)
                    max_delay = delay;
            }
        }
        // normalize
        for (auto &iter : ratio)
            iter.second = iter.second / max_delay;
    }
}

void GlobalRouter::route_net(Net &net, uint32_t it) {
    const auto &src = net[0].node;
    if (src == nullptr)
        throw ::runtime_error("unable to find src when route net");
    for (uint32_t i = 1; i < net.size(); i++) {
        auto const &sink_node = net[i];
        // find the routes
        if (sink_node.name[0] == 'r') {
            ::pair<uint32_t, uint32_t> end = {sink_node.x, sink_node.y};
            if (it != 0 && sink_node.node == nullptr) {
                // previous attempts have failed;
                // don't clear the previous routing table so that it will
                // increase the cost function to re-use the same route.
                fail_count_++;
                continue;
            }
            (void)end;

        } else {
            if (sink_node.node == nullptr)
                throw ::runtime_error("unable to find node for block"
                                      " " + sink_node.name);
        }
    }
}

void GlobalRouter::update_cost_table() {

}

void GlobalRouter::assign_routes() {

}

std::function<uint32_t(const std::shared_ptr<Node> &)>
GlobalRouter::create_cost_function() {
    return [](const std::shared_ptr<Node> &) -> uint32_t { return 0; };
}

void GlobalRouter::index_node_history_table() {

}

void GlobalRouter::update_node_history_table() {

}
